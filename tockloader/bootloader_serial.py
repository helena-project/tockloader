'''
Interface with a board over serial that is using the
[Tock Bootloader](https://github.com/tock/tock-bootloader).
'''

import atexit
import crcmod
import datetime
import hashlib
import json
import logging
import os
import platform
import socket
import struct
import sys
import time
import threading

# Although Windows is not supported actively, this allow features that "just
# work" to work on Windows.
if platform.system() != 'Windows':
	import fcntl

import serial
import serial.tools.list_ports
import serial.tools.miniterm

from . import helpers
from .board_interface import BoardInterface
from .exceptions import TockLoaderException

class BootloaderSerial(BoardInterface):
	'''
	Implementation of `BoardInterface` for the Tock Bootloader over serial.
	'''

	# "This was chosen as it is infrequent in .bin files" - immesys
	ESCAPE_CHAR = 0xFC

	# Commands from this tool to the bootloader.
	# The "X" commands are for external flash.
	COMMAND_PING               = 0x01
	COMMAND_INFO               = 0x03
	COMMAND_ID                 = 0x04
	COMMAND_RESET              = 0x05
	COMMAND_ERASE_PAGE         = 0x06
	COMMAND_WRITE_PAGE         = 0x07
	COMMAND_XEBLOCK            = 0x08
	COMMAND_XWPAGE             = 0x09
	COMMAND_CRCRX              = 0x10
	COMMAND_READ_RANGE         = 0x11
	COMMAND_XRRANGE            = 0x12
	COMMAND_SET_ATTRIBUTE      = 0x13
	COMMAND_GET_ATTRIBUTE      = 0x14
	COMMAND_CRC_INTERNAL_FLASH = 0x15
	COMMAND_CRCEF              = 0x16
	COMMAND_XEPAGE             = 0x17
	COMMAND_XFINIT             = 0x18
	COMMAND_CLKOUT             = 0x19
	COMMAND_WUSER              = 0x20
	COMMAND_CHANGE_BAUD_RATE   = 0x21
	COMMAND_EXIT               = 0x22
	COMMAND_SET_START_ADDRESS  = 0x23

	# Responses from the bootloader.
	RESPONSE_OVERFLOW           = 0x10
	RESPONSE_PONG               = 0x11
	RESPONSE_BADADDR            = 0x12
	RESPONSE_INTERROR           = 0x13
	RESPONSE_BADARGS            = 0x14
	RESPONSE_OK                 = 0x15
	RESPONSE_UNKNOWN            = 0x16
	RESPONSE_XFTIMEOUT          = 0x17
	RESPONSE_XFEPE              = 0x18
	RESPONSE_CRCRX              = 0x19
	RESPONSE_READ_RANGE         = 0x20
	RESPONSE_XRRANGE            = 0x21
	RESPONSE_GET_ATTRIBUTE      = 0x22
	RESPONSE_CRC_INTERNAL_FLASH = 0x23
	RESPONSE_CRCXF              = 0x24
	RESPONSE_INFO               = 0x25
	RESPONSE_CHANGE_BAUD_FAIL   = 0x26

	# Tell the bootloader to reset its buffer to handle a new command.
	SYNC_MESSAGE = bytes([0x00, 0xFC, 0x05])

	def __init__ (self, args):
		super().__init__(args)

		# The Tock serial bootloader only uses 512 byte pages to simplify the
		# implementations and reduce uncertainty. Chips implementing the
		# bootloader are expected to handle data being written or erased in 512
		# byte chunks.
		self.page_size = 512

	def _determine_port (self):
		'''
		Helper function to determine which serial port on the host to use to
		connect to the board.
		'''
		# Check to see if the user specified a serial port or a specific name,
		# or if we should find a serial port to use.
		if self.args.port == None:
			# The user did not specify a specific port to use, so we look for
			# something marked as "Tock". If we can't find something, we will
			# fall back to using any serial port.
			device_name = 'tock'
			must_match = False
			logging.info('No device name specified. Using default name "{}".'.format(device_name))
		else:
			# Since the user specified, make sure we connect to that particular
			# port or something that matches it.
			device_name = self.args.port
			must_match = True

		# Look for a matching port
		ports = sorted(list(serial.tools.list_ports.grep(device_name)))
		if must_match:
			# In the most specific case a user specified a full path that exists
			# and we should use that specific serial port. We need to do more checking, however, as
			# something like `/dev/ttyUSB5` will also match
			# `/dev/ttyUSB55`, but it is clear that user expected to use the
			# serial port specified by the full path.
			for i,p in enumerate(ports):
				if p.device == device_name:
					# We found an exact name match. Use that.
					index = i
					break
			else:
				# We found no match. If we get here, then the user did not
				# specify a full path to a serial port, and we couldn't find
				# anything on the board that matches what they specified (which
				# may have just been a short name). Since the user put in the
				# effort of specifically choosing a port, we error here rather
				# than just (arbitrarily) choosing something they didn't
				# specify.
				raise TockLoaderException('Could not find a board matching "{}".'.format(device_name))
		elif len(ports) == 1:
			# Easy case, use the one that matches.
			index = 0
		elif len(ports) > 1:
			# If we get multiple matches then we ask the user to choose from a
			# list.
			index = helpers.menu(ports,
				                 return_type='index',
				                 title='Multiple serial port options found. Which would you like to use?')
		else:
			# Just find any port. If one, use that. If multiple, ask user.
			ports = list(serial.tools.list_ports.comports())
			# Macs will report Bluetooth devices with serial, which is
			# almost certainly never what you want, so drop those.
			ports = [p for p in ports if 'Bluetooth-Incoming-Port' not in p.device]

			if len(ports) == 0:
				raise TockLoaderException('No serial ports found. Is the board connected?')

			logging.info('No serial port with device name "{}" found.'.format(device_name))
			logging.info('Found {} serial port{}.'.format(len(ports), ('s', '')[len(ports) == 1]))

			if len(ports) == 1:
				index = 0
			else:
				index = helpers.menu(ports,
					                 return_type='index',
					                 title='Multiple serial port options found. Which would you like to use?')

		# Choose port. This should be a serial.ListPortInfo type.
		port = ports[index]

		logging.info('Using "{}".'.format(port))

		# Save the serial number. This might help us reconnect later if say we
		# have to boot into the bootloader and the OS assigns a new port name
		# to the same physical board.
		self.sp_serial_number = port.serial_number

		# Improve UI for users
		helpers.set_terminal_title_from_port_info(port)

		# Return serial port device name
		return port.device

	def _configure_serial_port (self, port):
		'''
		Helper function to configure the serial port so we can read/write with
		it.
		'''
		# Open the actual serial port
		self.sp = serial.Serial()

		# We need to monkey patch the serial library so that it does not clear
		# our receive buffer. For FTDI devices this is not necessary. However,
		# for CDC-ACM devices, the board can send back data before we are
		# finished configuring it. We don't want to lose that data, so we
		# replace the `reset_input_buffer()` function with a no-op.
		def dummy_function():
			pass
		self.sp.reset_input_buffer = dummy_function

		self.sp.port = port
		self.sp.baudrate = 115200
		self.sp.parity=serial.PARITY_NONE
		self.sp.stopbits=1
		self.sp.xonxoff=0
		self.sp.rtscts=0
		self.sp.timeout=0.5
		# Try to set initial conditions, but not all platforms support them.
		# https://github.com/pyserial/pyserial/issues/124#issuecomment-227235402
		self.sp.dtr = 0
		self.sp.rts = 0

	def open_link_to_board (self, listen=False):
		'''
		Open the serial port to the chip/bootloader.

		Also sets up a local port for determining when two Tockloader instances
		are running simultaneously.

		Set the argument `listen` to true if the serial port is being setup
		because we are planning to run `run_terminal`.
		'''
		port = self._determine_port()
		self._configure_serial_port(port)

		# Only one process at a time can talk to a serial port (reliably).
		# Before connecting, check whether there is another tockloader process
		# running, and if it's a listen, pause that listen (unless we are also
		# doing a listen), otherwise bail out.
		self.comm_path = '/tmp/tockloader.' + self._get_serial_port_hash()
		if os.path.exists(self.comm_path):
			# Open a socket to the other tockloader instance if one exists.
			self.client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
			try:
				self.client_sock.connect(self.comm_path)
			except ConnectionRefusedError:
				logging.warning('Found stale tockloader server, removing.')
				logging.warning('This may occur if a previous tockloader instance crashed.')
				os.unlink(self.comm_path)
				self.client_sock = None
		else:
			self.client_sock = None

		# Check if another tockloader instance exists based on whether we were
		# able to create a socket to it.
		if self.client_sock:
			# If we could connect, and we are trying to do a listen on the same
			# serial port, then we should exit and notify the user there is
			# already an active tockloader process.
			if listen:
				# We tell the other tockloader not to mind us and then print
				# an error to the user.
				self.client_sock.sendall('Version 1\n'.encode('utf-8'))
				self.client_sock.sendall('Ignore\n'.encode('utf-8'))
				self.client_sock.close()
				raise TockLoaderException('Another tockloader process is already running')

			self.client_sock.sendall('Version 1\n'.encode('utf-8'))
			self.client_sock.sendall('Stop Listening\n'.encode('utf-8'))
			r = ''
			while '\n' not in r:
				r += self.client_sock.recv(100).decode('utf-8')
			if r == 'Busy\n':
				raise TockLoaderException('Another tockloader process is active on this serial port')
			elif r == 'Killing\n':
				def restart_listener(path):
					sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
					try:
						sock.connect(path)
						sock.sendall('Version 1\n'.encode('utf-8'))
						sock.sendall('Start Listening\n'.encode('utf-8'))
						sock.close()
						logging.info('Resumed other tockloader listen session')
					except:
						logging.warning('Error restarting other tockloader listen process.')
						logging.warning('You may need to manually begin listening again.')
				atexit.register(restart_listener, self.comm_path)
				self.client_sock.close()
				while os.path.exists(self.comm_path):
					time.sleep(.1)
				logging.info('Paused an active tockloader listen in another session.')
			else:
				raise TockLoaderException('Internal error: Got >{}< from IPC'.format(r))
		else:
			self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
			flags = fcntl.fcntl(self.server_sock, fcntl.F_GETFD)
			fcntl.fcntl(self.server_sock, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
			self.server_sock.bind(self.comm_path)
			self.server_sock.listen(1)
			self.server_event = threading.Event()
			self.server_thread = threading.Thread(
					target=self._server_thread,
					daemon=True,
					)
			self.server_thread.start()
			def server_cleanup():
				if self.server_sock is not None:
					self.server_sock.close()
					os.unlink(self.comm_path)
			atexit.register(server_cleanup)

			if hasattr(self.args, 'wait_to_listen') and self.args.wait_to_listen:
				logging.info('Waiting for other tockloader to finish before listening')
				self.server_event.wait()
				logging.info('Resuming listening...')

		self.sp.open()

		# Do a delay if we are skipping the bootloader entry process (which
		# would normally have a delay in it). We need to send a dummy message
		# because that seems to cause the serial to reset the board, and then
		# wait to make sure the bootloader is booted and ready.
		if hasattr(self.args, 'no_bootloader_entry') and self.args.no_bootloader_entry:
			# Writing a bogus message seems to start the counter.
			self.sp.write(self.SYNC_MESSAGE)
			time.sleep(0.1)

	# While tockloader has a serial connection open, it leaves a unix socket
	# open for other tockloader processes. For most of the time, this will
	# simply report 'Busy\n' and new tockloader processes will back off and not
	# steal the serial port. If miniterm is active, however, this process will
	# send back 'Killing\n', terminate this process (due to miniterm
	# architecture, there's no good way to programmatically kill & restart
	# miniterm threads), and restart this process with --wait-to-listen
	def _server_thread (self):
		while True:
			connection, client_address = self.server_sock.accept()
			r = ''
			while '\n' not in r:
				r += connection.recv(100).decode('utf-8')
			if r[:len('Version 1\n')] != 'Version 1\n':
				logging.warning('Got unexpected connection: >{}< ; dropping'.format(r))
				connection.close()
				continue

			r = r[len('Version 1\n'):]
			while '\n' not in r:
				r += connection.recv(100).decode('utf-8')

			if r == 'Start Listening\n':
				self.server_event.set()
				continue

			if r == 'Ignore\n':
				# The other tockloader was just checking to see if we exist.
				# We can just close the connection on our end and keep waiting.
				connection.close()
				continue

			if r != 'Stop Listening\n':
				logging.warning('Got unexpected command: >{}< ; dropping'.format(r))
				connection.close()
				continue

			if not hasattr(self, 'miniterm'):
				# Running something other than listen, reject other tockloader
				connection.sendall('Busy\n'.encode('utf-8'))
				connection.close()
				continue

			# Since there's no great way to kill & restart miniterm, we just
			# redo the whole process, only tacking on a --wait-to-listen
			logging.info('Received request to pause from another tockloader process. Disconnecting...')
			# And let them know we've progressed
			connection.sendall('Killing\n'.encode('utf-8'))
			connection.close()
			self.server_sock.close()
			os.unlink(self.comm_path)
			# Need to run miniterm's atexit handler
			self.miniterm.console.cleanup()

			# Prep arguments for next tockloader
			args = list(sys.argv)

			# Need to wait for the process that killed this session to be done
			args.append('--wait-to-listen')

			# If there are multiple devices plugged in, we want the resuming
			# tockloader to auto-choose the one it was already listening to.
			# We can blindly append here as argument parsing will use the last
			# -p option it finds, thus no warning/error if the user had already
			# supplied a -p when first invoked
			args.append('-p')
			args.append(self.sp.port)

			os.execvp(args[0], args)

	def _get_serial_port_hash (self):
		'''
		Get an identifier that will be consistent for this serial port on this
		machine that is also guaranteed to not have any special characters (like
		slashes) that would interfere with using as a file name.
		'''
		return hashlib.sha1(self.sp.port.encode('utf-8')).hexdigest()

	def _toggle_bootloader_entry_DTR_RTS (self):
		'''
		Use the DTR and RTS lines on UART to reset the chip and assert the
		bootloader select pin to enter bootloader mode so that the chip will
		start in bootloader mode.
		'''
		# Reset the SAM4L
		self.sp.dtr = 1
		# Set RTS to make the SAM4L go into bootloader mode
		self.sp.rts = 1
		# Wait for the reset to take effect
		time.sleep(0.1)
		# Let the SAM4L startup
		self.sp.dtr = 0
		# Wait for 500 ms to make sure the bootloader enters bootloader mode
		time.sleep(0.5)
		# The select line can go back high
		self.sp.rts = 0

	def _toggle_bootloader_entry_baud_rate (self):
		'''
		Set the baud rate to 1200 so that the chip will restart into the
		bootloader (if that feature exists).

		Returns `True` if it successfully started the bootloader, `False`
		otherwise.
		'''

		# Change the baud rate to tell the board to reset into the bootloader.
		self.sp.baudrate = 1200

		# Now try to read from the serial port. If the changed baud rate caused
		# the chip to reset into bootloader mode, this read should fail. If it
		# doesn't fail, then either this chip doesn't support the baud rate chip
		# (e.g. it has an FTDI chip) or it is already in the bootloader.
		try:
			# Give the chip some time to reset
			time.sleep(0.1)
			# Read which should timeout quickly.
			test_read = self.sp.read(10)
			# If we get here, looks like this entry mode won't work, so we can
			# exit now.
			if self.args.debug:
				logging.debug('Baud rate bootloader entry no-op.')
				if len(test_read) > 0:
					logging.debug('Read "{}" from board'.format(test_read))

			# Need to reset the baud rate to its original value.
			self.sp.baudrate = 115200
			return False
		except:
			# Read failed. This should mean the chip reset. Continue with this
			# function.
			pass

		# Wait for the serial port to re-appear, aka the bootloader has started.
		logging.info('Waiting for the bootloader to start')

		for i in range(0, 30):
			# We start by sleeping. On Linux the serial port does not
			# immediately disappear, so if we do not wait we will immediately
			# discover the serial port again. So we have to wait to give the OS
			# a chance to remove the serial port before we try to re-discover it
			# once the bootloader has started.
			time.sleep(0.5)

			# Try to increase reliability by trying different ways of
			# re-discovering the serial port.
			if i < 10:
				# To start we try to find a serial device with the same serial
				# number as the one that we connected to originally.
				ports = list(serial.tools.list_ports.grep(self.sp_serial_number))

			elif i < 20:
				# If that isn't working, it is possible that the serial number
				# is different between the kernel (which we connected to first)
				# and the bootloader (which is what is now setting up the serial
				# port). The bootloader should have the name "tock" in it,
				# however, so we look for that.
				#
				# Note, this can be problematic if the user has multiple tock
				# boards connected to the computer, since this might find a
				# different board leading to weird behavior.
				ports = list(serial.tools.list_ports.grep('tock'))

			else:
				# In the last case, we try to connect to any available serial port
				# and hope that it is the tock bootloader.
				ports = list(serial.tools.list_ports.comports())
				# Macs will report Bluetooth devices with serial, which is
				# almost certainly never what you want, so drop those.
				ports = [p for p in ports if 'Bluetooth-Incoming-Port' not in p.device]

			if len(ports) > 0:
				if self.args.debug:
					logging.debug('  On iteration {} found {} port{}'.format(i, len(ports), helpers.plural(len(ports))))
				break
			else:
				if self.args.debug:
					logging.debug('  Waited iteration {}... Found 0 ports'.format(i))

		else:
			raise TockLoaderException('Bootloader did not start')

		# Use the first port.
		port = ports[0].device

		if self.args.debug:
			logging.debug('  Using port {} for the bootloader'.format(port))

		self._configure_serial_port(port)
		self.sp.open()

		# Board restarted into the bootloader (or at least a new serial port)
		# and we re-setup self.sp to use it.
		return True

	def enter_bootloader_mode (self):
		'''
		Reset the chip and assert the bootloader select pin to enter bootloader
		mode. Handle retries if necessary.
		'''
		# Try baud rate trick first.
		entered_bootloader = self._toggle_bootloader_entry_baud_rate()
		if not entered_bootloader:
			# If that didn't work, either because the bootloader already active
			# or board doesn't support it, try the DTR/RTS method.
			try:
				# Wrap in try block because this can fail if the serial port was
				# _actually_ closed in the
				# `_toggle_bootloader_entry_baud_rate()` step, but that function
				# did not detect it. This code is all a bunch of data races and
				# balancing not making users wait a long time. So we insert
				# various sleeps, but they may not always be long enough, so
				# there can be false/missed detections.
				self._toggle_bootloader_entry_DTR_RTS()
			except:
				# If we could not toggle DTR/RTS then there is something wrong
				# with the serial port. Hopefully this means that we are in the
				# bootloader and can continue normally. If not, then the
				# PING/PONG check below should catch it. Let's be optimistic.
				#
				# Retry existing serial port.
				self._configure_serial_port(self.sp.port)
				self.sp.open()


		# Make sure the bootloader is actually active and we can talk to it.
		try:
			self._ping_bootloader_and_wait_for_response()
		except KeyboardInterrupt:
			raise TockLoaderException('Exiting.')
		except:
			try:
				# Give it another go
				time.sleep(1)
				self._toggle_bootloader_entry()
				self._ping_bootloader_and_wait_for_response()
			except KeyboardInterrupt:
				raise TockLoaderException('Exiting.')
			except:
				logging.error('Error connecting to bootloader. No "pong" received.')
				logging.error('Things that could be wrong:')
				logging.error('  - The bootloader is not flashed on the chip')
				logging.error('  - The DTR/RTS lines are not working')
				logging.error('  - The serial port being used is incorrect')
				logging.error('  - The bootloader API has changed')
				logging.error('  - There is a bug in this script')
				raise TockLoaderException('Could not attach to the bootloader')

		# Speculatively try to get a faster baud rate.
		if self.args.baud_rate != 115200:
			self._change_baud_rate(self.args.baud_rate)

	def exit_bootloader_mode (self):
		'''
		Reset the chip to exit bootloader mode.
		'''
		if self.args.jtag:
			return

		# Try to exit with a command to the bootloader. This is a relatively
		# new feature (as of bootloader v1.1.0), so this may not work on many
		# boards.
		self._exit_bootloader()

		# Also try the "reset to exit" method. This works if the DTR line is
		# connected to the reset pin on the MCU.
		try:
			# Wrap all of this in a try block in case the `_exit_bootloader()`
			# method worked, at which point the serial port may be invalid when
			# we get here.
			self.sp.dtr = 1
			# Make sure this line is de-asserted (high)
			self.sp.rts = 0
			# Let the reset take effect
			time.sleep(0.1)
			# Let the SAM4L startup
			self.sp.dtr = 0
		except:
			# I've seen OSError and BrokenPipeError get thrown if the serial
			# port is invalid. I'm not sure there is any viable way to handle
			# different errors, and it probably doesn't matter. These are basic
			# UART config settings, and if they don't work then the chip
			# hopefully has exited the bootloader.
			return

	def _ping_bootloader_and_wait_for_response (self):
		'''
		Throws an exception if the device does not respond with a PONG.
		'''
		for i in range(30):
			# Try to ping the SAM4L to ensure it is in bootloader mode
			ping_pkt = bytes([self.ESCAPE_CHAR, self.COMMAND_PING])
			self.sp.write(ping_pkt)

			# Read much more than we need in case something got in the
			# serial channel that we need to clear.
			ret = self.sp.read(200)

			if len(ret) == 2 and ret[1] == self.RESPONSE_PONG:
				return
		raise TockLoaderException('No PONG received')

	def _issue_command (self, command, message, sync, response_len, response_code, show_errors=True):
		'''
		Setup a command to send to the bootloader and handle the response.
		'''

		# Generate the message to send to the bootloader
		escaped_message = message.replace(bytes([self.ESCAPE_CHAR]), bytes([self.ESCAPE_CHAR, self.ESCAPE_CHAR]))
		pkt = escaped_message + bytes([self.ESCAPE_CHAR, command])

		# If there should be a sync/reset message, prepend the outgoing message
		# with it.
		if sync:
			pkt = self.SYNC_MESSAGE + pkt

		# Write the command message.
		self.sp.write(pkt)

		# Response has a two byte header, then response_len bytes. Keeping in
		# mind that bytes can be escaped, keep track of how how many bytes we
		# need to read in.
		bytes_to_read = 2 + response_len

		# Loop to read in that number of bytes. Only unescape the newest bytes.
		# Start with the header we know we are going to get. This makes
		# checking for dangling escape characters easier.
		ret = self.sp.read(2)

		# Check for errors in the header we just got. We have to stop at this
		# point since otherwise we loop waiting on data we will not get.
		if len(ret) < 2:
			if show_errors:
				logging.error('No response after issuing command')
			return (False, bytes())
		if ret[0] != self.ESCAPE_CHAR:
			if show_errors:
				logging.error('Invalid response from bootloader (no escape character)')
			return (False, ret[0:2])
		if ret[1] != response_code:
			if show_errors:
				logging.error('Expected return type {:x}, got return {:x}'.format(response_code, ret[1]))
			return (False, ret[0:2])

		while bytes_to_read - len(ret) > 0:
			new_data = self.sp.read(bytes_to_read - len(ret))

			# Escape characters are tricky here. We need to make sure that if
			# the last character is an an escape character that it isn't
			# escaping the next character we haven't read yet.
			if new_data.count(self.ESCAPE_CHAR) % 2 == 1:
				# Odd number of escape characters. These can only come in pairs,
				# so read another byte.
				new_data += self.sp.read(1)

			# De-escape, and add to array of read in bytes.
			ret += new_data.replace(bytes([self.ESCAPE_CHAR, self.ESCAPE_CHAR]), bytes([self.ESCAPE_CHAR]))

		if len(ret) != 2 + response_len:
			if show_errors:
				logging.error('Incorrect number of bytes received. Expected {}, got {}.'.format(2+response_len, len(ret)))
			return (False, ret[0:2])

		return (True, ret[2:])

	def _change_baud_rate (self, baud_rate):
		'''
		If the bootloader on the board supports it and if it succeeds, try to
		increase the baud rate to make everything faster.
		'''
		pkt = struct.pack('<BI', 0x01, baud_rate)
		success, ret = self._issue_command(self.COMMAND_CHANGE_BAUD_RATE, pkt, True, 0, self.RESPONSE_OK, show_errors=False)

		if success:
			# The bootloader is new enough to support this.
			# Increase the baud rate
			self.sp.baudrate = baud_rate
			# Now confirm that everything is working.
			pkt = struct.pack('<BI', 0x02, baud_rate)
			success, ret = self._issue_command(self.COMMAND_CHANGE_BAUD_RATE, pkt, False, 0, self.RESPONSE_OK, show_errors=False)

			if not success:
				# Something went wrong. Go back to old baud rate
				self.sp.baudrate = 115200

	def _exit_bootloader (self):
		'''
		Tell the bootloader on the board to exit so the main software can run.

		This uses a command sent over the serial port to the bootloader.
		'''
		exit_pkt = bytes([self.ESCAPE_CHAR, self.COMMAND_EXIT])
		self.sp.write(exit_pkt)

	def flash_binary (self, address, binary, pad=True):
		'''
		Write pages until a binary has been flashed. binary must have a length
		that is a multiple of page size.
		'''
		# Make sure the binary is a multiple of the page size by padding 0xFFs
		if len(binary) % self.page_size != 0:
			remaining = self.page_size - (len(binary) % self.page_size)
			if pad:
				binary += bytes([0xFF]*remaining)
				logging.info('Padding binary with {} 0xFFs.'.format(remaining))
			else:
				# Don't pad, actually use the bytes already on the chip
				missing = self.read_range(address + len(binary), remaining)
				binary += missing
				logging.info('Padding binary with {} bytes already on chip.'.format(remaining))

		# Loop through the binary by pages at a time until it has been flashed
		# to the chip.
		for i in range(len(binary) // self.page_size):
			# Create the packet that we send to the bootloader. First four
			# bytes are the address of the page.
			pkt = struct.pack('<I', address + (i*self.page_size))

			# Next are the bytes that go into the page.
			pkt += binary[i*self.page_size: (i+1)*self.page_size]

			# Write to bootloader
			success, ret = self._issue_command(self.COMMAND_WRITE_PAGE, pkt, True, 0, self.RESPONSE_OK)

			if not success:
				logging.error('Error when flashing page')
				if ret[1] == self.RESPONSE_BADADDR:
					raise TockLoaderException('Error: RESPONSE_BADADDR: Invalid address for page to write (address: 0x{:X}'.format(address + (i*self.page_size)))
				elif ret[1] == self.RESPONSE_INTERROR:
					raise TockLoaderException('Error: RESPONSE_INTERROR: Internal error when writing flash')
				elif ret[1] == self.RESPONSE_BADARGS:
					raise TockLoaderException('Error: RESPONSE_BADARGS: Invalid length for flash page write')
				else:
					raise TockLoaderException('Error: 0x{:X}'.format(ret[1]))

		# And check the CRC
		self._check_crc(address, binary)

	def read_range (self, address, length):
		# Can only read up to 4095 bytes at a time.
		MAX_READ = 4095
		read = bytes()
		this_length = 0
		remaining = length
		while remaining > 0:
			if remaining > MAX_READ:
				this_length = MAX_READ
				remaining -= MAX_READ
			else:
				this_length = remaining
				remaining = 0

			message = struct.pack('<IH', address, this_length)
			success, flash = self._issue_command(self.COMMAND_READ_RANGE, message, True, this_length, self.RESPONSE_READ_RANGE)

			if not success:
				return b''
			else:
				read += flash

			address += this_length

		return read

	def erase_page (self, address):
		message = struct.pack('<I', address)
		success, ret = self._issue_command(self.COMMAND_ERASE_PAGE, message, True, 0, self.RESPONSE_OK)

		if not success:
			if ret[1] == self.RESPONSE_BADADDR:
				raise TockLoaderException('Error: Page erase address was not on a page boundary.')
			elif ret[1] == self.RESPONSE_BADARGS:
				raise TockLoaderException('Error: Need to supply erase page with correct 4 byte address.')
			elif ret[1] == self.RESPONSE_INTERROR:
				raise TockLoaderException('Error: Internal error when erasing flash page.')
			else:
				raise TockLoaderException('Error: 0x{:X}'.format(ret[1]))

	def set_start_address (self, address):
		message = struct.pack('<I', address)
		success, ret = self._issue_command(self.COMMAND_SET_START_ADDRESS, message, True, 0, self.RESPONSE_OK)

		if not success:
			if ret[1] == self.RESPONSE_BADARGS:
				raise TockLoaderException('Error: Need to supply start address.')
			else:
				raise TockLoaderException('Error: 0x{:X}'.format(ret[1]))

	def _get_crc_internal_flash (self, address, length):
		'''
		Get the bootloader to compute a CRC.
		'''
		message = struct.pack('<II', address, length)
		success, crc = self._issue_command(self.COMMAND_CRC_INTERNAL_FLASH, message, True, 4, self.RESPONSE_CRC_INTERNAL_FLASH)

		# There is a bug in a version of the bootloader where the CRC returns 6
		# bytes and not just 4. Need to read just in case to grab those extra
		# bytes.
		self.sp.read(2)

		if not success:
			if crc[1] == self.RESPONSE_BADADDR:
				raise TockLoaderException('Error: RESPONSE_BADADDR: Invalid address for CRC (address: 0x{:X})'.format(address))
			elif crc[1] == self.RESPONSE_BADARGS:
				raise TockLoaderException('Error: RESPONSE_BADARGS: Invalid length for CRC check')
			else:
				raise TockLoaderException('Error: 0x{:X}'.format(crc[1]))

		return crc

	def _check_crc (self, address, binary):
		'''
		Compares the CRC of the local binary to the one calculated by the
		bootloader.
		'''
		# Check the CRC
		crc_data = self._get_crc_internal_flash(address, len(binary))

		# Now interpret the returned bytes as the CRC
		crc_bootloader = struct.unpack('<I', crc_data[0:4])[0]

		# Calculate the CRC locally
		crc_function = crcmod.mkCrcFun(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)
		crc_loader = crc_function(binary, 0)

		if crc_bootloader != crc_loader:
			raise TockLoaderException('Error: CRC check failed. Expected: 0x{:04x}, Got: 0x{:04x}'.format(crc_loader, crc_bootloader))
		else:
			logging.info('CRC check passed. Binaries successfully loaded.')

	def get_attribute (self, index):
		message = struct.pack('<B', index)
		success, ret = self._issue_command(self.COMMAND_GET_ATTRIBUTE, message, True, 64, self.RESPONSE_GET_ATTRIBUTE)

		if not success:
			if ret[1] == self.RESPONSE_BADADDR:
				raise TockLoaderException('Error: Attribute number is invalid.')
			elif ret[1] == self.RESPONSE_BADARGS:
				raise TockLoaderException('Error: Need to supply a correct attribute index.')
			else:
				raise TockLoaderException('Error: 0x{:X}'.format(ret[1]))
		return self._decode_attribute(ret)

	def get_all_attributes (self):
		attributes = []
		for index in range(0, 16):
			attributes.append(self.get_attribute(index))
		return attributes

	def set_attribute (self, index, raw):
		message = struct.pack('<B', index) + raw
		success, ret = self._issue_command(self.COMMAND_SET_ATTRIBUTE, message, True, 0, self.RESPONSE_OK)

		if not success:
			if ret[1] == self.RESPONSE_BADADDR:
				raise TockLoaderException('Error: Attribute number is invalid.')
			elif ret[1] == self.RESPONSE_BADARGS:
				raise TockLoaderException('Error: Wrong length of attribute set packet.')
			elif ret[1] == self.RESPONSE_INTERROR:
				raise TockLoaderException('Error: Internal error when setting attribute.')
			else:
				raise TockLoaderException('Error: 0x{:X}'.format(ret[1]))

	def bootloader_is_present (self):
		'''
		For this communication protocol we can safely say the bootloader is
		present.
		'''
		return True

	def get_bootloader_version (self):
		success, ret = self._issue_command(self.COMMAND_INFO, bytes(), True, 193, self.RESPONSE_INFO)

		if not success:
			raise TockLoaderException('Error: 0x{:X}'.format(ret[1]))

		length = ret[0]
		json_data = ret[1:1+length].decode('utf-8')
		try:
			info = json.loads(json_data)

			if self.args.debug:
				logging.debug(info)

			return info['version']
		except:
			# Could not get a valid version from the board.
			# In this case we don't know what the version is.
			return None

	def determine_current_board (self):
		if self.board and self.arch and self.page_size>0:
			# These are already set! Yay we are done.
			return

		# If settings aren't set yet, we need to see if they are set on the
		# board. The primary (only?) way to do this is to look at attributes.
		attributes = self.get_all_attributes()
		for attribute in attributes:
			if attribute and attribute['key'] == 'board' and self.board == None:
				self.board = attribute['value']
			if attribute and attribute['key'] == 'arch' and self.arch == None:
				self.arch = attribute['value']
			if attribute and attribute['key'] == 'pagesize' and self.page_size == 0:
				self.page_size = attribute['value']

		# We might need to fill in if we only got a "board" attribute.
		self._configure_from_known_boards()

		# Check that we learned what we needed to learn.
		if self.board == None:
			logging.error('The bootloader does not have a "board" attribute.')
			logging.error('Please update the bootloader or specify a board; e.g. --board hail')
		if self.arch == None:
			logging.error('The bootloader does not have an "arch" attribute.')
			logging.error('Please update the bootloader or specify an arch; e.g. --arch cortex-m4')
		if self.page_size == 0:
			logging.error('The bootloader does not have an "pagesize" attribute.')
			logging.error('Please update the bootloader or specify a page size for flash; e.g. --page-size 512')

		if self.board == None or self.arch == None or self.page_size == 0:
			raise TockLoaderException('Could not determine the board and/or architecture')


	def run_terminal(self):
		'''
		Run miniterm for receiving data from the board.
		'''
		logging.info('Listening for serial output.')

		# Create a custom filter for miniterm that prepends the date.
		class timestamper(serial.tools.miniterm.Transform):
			'''Prepend output lines with timestamp'''

			def __init__(self):
				self.last = None

			def rx(self, text):
				# Only prepend the date if the last character returned
				# was a \n.
				last = self.last
				self.last = text[-1]
				if last == '\n' or last == None:
					return '[{}] {}'.format(datetime.datetime.now(), text)
				else:
					return text

		# Create a custom filter for miniterm that prepends the number of
		# printed messages.
		class counter(serial.tools.miniterm.Transform):
			'''Prepend output lines with a message count'''

			def __init__(self):
				self.last = None
				self.count = 0

			def rx(self, text):
				# Only prepend the date if the last character returned
				# was a \n.
				last = self.last
				self.last = text[-1]
				if last == '\n' or last == None:
					count = self.count
					self.count += 1
					return '[{:>6}] {}'.format(count, text)
				else:
					return text

		# Add our custom filter to the list that miniterm knows about
		serial.tools.miniterm.TRANSFORMATIONS['timestamper'] = timestamper
		serial.tools.miniterm.TRANSFORMATIONS['counter'] = counter

		# Choose the miniterm filter we want to use. Normally we just use
		# default, which just prints to terminal, but we can also print
		# timestamps.
		filters = ['default']
		if self.args.timestamp:
			filters.append('timestamper')
		if self.args.count:
			filters.append('counter')

		# Use trusty miniterm
		self.miniterm = serial.tools.miniterm.Miniterm(
			self.sp,
			echo=False,
			eol='crlf',
			filters=filters)

		# Ctrl+c to exit.
		self.miniterm.exit_character = serial.tools.miniterm.unichr(0x03)
		self.miniterm.set_rx_encoding('UTF-8')
		self.miniterm.set_tx_encoding('UTF-8')

		self.miniterm.start()
		try:
			self.miniterm.join(True)
		except KeyboardInterrupt:
			pass

		self.miniterm.stop()
		self.miniterm.join()
		self.miniterm.close()
