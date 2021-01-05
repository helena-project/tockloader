'''
Generic interface for communicating with boards.

While it would be nice if there was only a single method to communicate with
boards, in practice that is not feasible. So, this file includes the interface
that different communication methods must implement to effectively support
tockloader.
'''

import logging
import os

class BoardInterface:
	'''
	Base class for interacting with hardware boards. All of the class functions
	should be overridden to support a new method of interacting with a board.
	'''

	KNOWN_BOARDS = {
		'hail': {'description': 'Hail development module.',
		         'arch': 'cortex-m4',
		         'jlink_device': 'ATSAM4LC8C',
		         'page_size': 512},
		'imix': {'description': 'Low-power IoT research platform',
		         'arch': 'cortex-m4',
		         'jlink_device': 'ATSAM4LC8C',
		         'page_size': 512},
		'nrf51dk': {'description': 'Nordic nRF51-based development kit',
		            'arch': 'cortex-m0',
		            'jlink_device': 'nrf51422',
		            'page_size': 1024,
		            'openocd': 'nordic_nrf51_dk.cfg',
		            'openocd_options': ['workareazero']},
		'nrf52dk': {'description': 'Nordic nRF52-based development kit',
		            'arch': 'cortex-m4',
		            'jlink_device': 'nrf52',
		            'page_size': 4096,
		            'openocd': 'nordic_nrf52_dk.cfg'},
		'nano33ble': {'description': 'Arduino Nano 33 BLE board',
		              'arch': 'cortex-m4'},
		'launchxl-cc26x2r1': {'description': 'TI CC26x2-based launchpad',
		                      'arch': 'cortex-m4',
		                      'page_size': 512,
		                      'jlink_device': 'cc2652r1f',
		                      'jlink_speed': 4000,
		                      'jlink_if': 'jtag',
		                      'openocd': 'ti_cc26x2_launchpad.cfg',
		                      'openocd_options': ['noreset', 'resume'],
		                      'openocd_commands': {'program': 'flash write_image erase {{binary}} {address:#x};\
		                                                       verify_image {{binary}} {address:#x};'}},
		'ek-tm4c1294xl': {'description': 'TI TM4C1294-based launchpad',
		                  'arch': 'cortex-m4',
		                  'page_size': 512,
		                  'openocd': 'ek-tm4c1294xl.cfg'},
		'arty': {'description': 'Arty FPGA running SiFive RISC-V core',
		         'arch': 'rv32imac',
		         'apps_start_address': 0x40430000,
		         # arty exposes just the flash to openocd, this does the mapping
		         # from the address map to what openocd must use.
		         'address_translator': lambda addr: addr - 0x40000000,
		         'page_size': 512,
		         'openocd': 'external', # No supported board in openocd proper
		         'openocd_options': ['nocmdprefix'],
		         'openocd_prefix': 'source [find interface/ftdi/digilent-hs1.cfg];\
		                            ftdi_device_desc \\"Digilent USB Device\\";\
		                            adapter_khz 10000;\
		                            transport select jtag;\
		                            source [find cpld/xilinx-xc7.cfg];\
		                            source [find cpld/jtagspi.cfg];\
		                            proc jtagspi_read {{fname offset len}} {{\
		                              global _FLASHNAME;\
		                              flash read_bank $_FLASHNAME $fname $offset $len;\
		                            }};\
		                            init;\
		                            jtagspi_init 0 {bitfile};'
		                            .format(bitfile=os.path.join( # Need path to bscan_spi_xc7a100t.bit
		                            	os.path.dirname(os.path.realpath(__file__)),
		            	                '..', 'bitfiles', 'bscan_spi_xc7a100t.bit')),
		         'openocd_commands': {'program': 'jtagspi_program {{binary}} {address:#x};',
		                              'read': 'jtagspi_read {{binary}} {address:#x} {length};',
		                              'erase': 'flash fillb {address:#x} 0x00 512;'}},
        'stm32f3discovery': {'description': 'STM32F3-based Discovery Boards',
                             'arch': 'cortex-m4',
                             'apps_start_address': 0x08020000,
                             'page_size': 2048,
                             'openocd': 'external',
                             'openocd_prefix': 'interface hla; \
                                                hla_layout stlink; \
                                                hla_device_desc "ST-LINK/V2-1"; \
                                                hla_vid_pid 0x0483 0x374b; \
                                                set WORKAREASIZE 0xC000; \
                                                source [find target/stm32f3x.cfg];'},
        'stm32f4discovery': {'description': 'STM32F4-based Discovery Boards',
                                'arch': 'cortex-m4',
                                'apps_start_address': 0x08040000,
                                'page_size': 2048,
                                'openocd': 'external',
                                'openocd_prefix': 'interface hla; \
                                                   hla_layout stlink; \
                                                   hla_device_desc "ST-LINK/V2-1"; \
                                                   hla_vid_pid 0x0483 0x374b; \
                                                   set WORKAREASIZE 0x40000; \
                                                   source [find target/stm32f4x.cfg];'},
		'nucleof4': {'description': 'STM32f4-based Nucleo development boards',
	                 'arch': 'cortex-m4',
	                 'apps_start_address': 0x08040000,
	                 'page_size': 2048,
	                 'openocd': 'st_nucleo_f4.cfg'},
		'hifive1': {'description': 'SiFive HiFive1 development board',
		            'arch': 'rv32imac',
		            'apps_start_address': 0x20430000,
		            'page_size': 512,
		            'openocd': 'sifive-hifive1.cfg'},
		'hifive1b': {'description': 'SiFive HiFive1b development board',
		             'arch': 'rv32imac',
		             'apps_start_address': 0x20040000,
		             'page_size': 512,
		             'jlink_device': 'FE310',
		             'jlink_if': 'jtag'},
		'edu-ciaa': {'description': 'Educational NXP board, from the CIAA project',
		             'arch': 'cortex-m4',
		             'page_size': 512,
		             'apps_start_address': 0x1a040000,
		             'openocd': 'ftdi_lpc4337.cfg',
		             'openocd_options': ['noreset'],
		             'openocd_commands': {'program': 'flash write_image erase {{binary}} {address:#x};verify_image {{binary}} {address:#x};',
		             'erase': 'flash fillb {address:#x} 0x00 512;'}},
	}

	def __init__ (self, args):
		self.args = args

		# These settings allow tockloader to correctly communicate with and
		# program the attached hardware platform. They can be set through the
		# following methods:
		#
		# 1. Command line arguments to tockloader.
		# 2. Hardcoded values in the `KNOWN_BOARDS` array.
		# 3. Attributes stored in flash on the hardware board itself.
		#
		# Tockloader looks for these setting in this order, and once a value has
		# been determined, tockloader will stop searching and use that value.
		# For example, if `arch` is set using the `--arch` argument to
		# tockloader, then that will override anything set in `KNOWN_BOARDS` or
		# in the on-board attributes.

		# Start by looking for command line arguments.
		self.board = getattr(self.args, 'board', None)
		self.arch = getattr(self.args, 'arch', None)
		self.apps_start_address = getattr(self.args, 'app_address', None)
		self.page_size = getattr(self.args, 'page_size', 0)

		# Next try to use `KNOWN_BOARDS`.
		self._configure_from_known_boards()

	def _configure_from_known_boards (self):
		'''
		If we know the name of the board we are interfacing with, this function
		tries to use the `KNOWN_BOARDS` array to populate other needed settings
		if they have not already been set from other methods.

		This can be used in multiple locations. First, it is used when
		tockloader first starts because if a user passes in the `--board`
		argument then we know the board and can try to pull in settings from
		KNOWN_BOARDS. Ideally, however, the user doesn't have to pass in any
		arguments, but then we won't know what board until after we have had a
		chance to read its attributes. The board at least needs the "board"
		attribute to be set, and then we can use KNOWN_BOARDS to fill in the
		rest.
		'''
		if self.board and self.board in self.KNOWN_BOARDS:
			board = self.KNOWN_BOARDS[self.board]
			if self.arch == None and 'arch' in board:
				self.arch = board['arch']
			if self.apps_start_address == None and 'apps_start_address' in board:
				self.apps_start_address = board['apps_start_address']
			if self.page_size == 0 and 'page_size' in board:
				self.page_size = board['page_size']

		# This init only includes the generic settings that all communication
		# methods need. There may be flags specific to a particular
		# communication interface.

	def open_link_to_board (self):
		'''
		Open a connection to the board.
		'''
		return

	def enter_bootloader_mode (self):
		'''
		Get to a mode where we can read & write flash.
		'''
		return

	def exit_bootloader_mode (self):
		'''
		Get out of bootloader mode and go back to running main code.
		'''
		return

	def flash_binary (self, address, binary):
		'''
		Write a binary to the address given.
		'''
		return

	def read_range (self, address, length):
		'''
		Read a specific range of flash.

		If this fails for some reason this should return an empty binary array.
		'''
		logging.debug('DEBUG => Read Range, address: {:#010x}, length: {}'.format(address, length))

	def erase_page (self, address):
		'''
		Erase a specific page of internal flash.
		'''
		return

	def get_attribute (self, index):
		'''
		Get a single attribute. Returns a dict with two keys: `key` and `value`.
		'''
		# Default implementation to get an attribute. Reads flash directly and
		# extracts the attribute.
		address = 0x600 + (64 * index)
		attribute_raw = self.read_range(address, 64)
		return self._decode_attribute(attribute_raw)

	def get_all_attributes (self):
		'''
		Get all attributes on a board. Returns an array of attribute dicts.
		'''
		# Read the entire block of attributes directly from flash.
		# This is much faster.
		def chunks(l, n):
			for i in range(0, len(l), n):
				yield l[i:i + n]
		raw = self.read_range(0x600, 64*16)
		return [self._decode_attribute(r) for r in chunks(raw, 64)]

	def set_attribute (self, index, raw):
		'''
		Set a single attribute.
		'''
		address = 0x600 + (64 * index)
		self.flash_binary(address, raw)

	def set_start_address (self, address):
		'''
		Set the address the bootloader jumps to to start the actual code.
		'''
		# This is only valid if there is a bootloader and this function is
		# re-implemented.
		raise TockLoaderException('No bootloader, cannot set start address.')

	def _decode_attribute (self, raw):
		try:
			key = raw[0:8].decode('utf-8').strip(bytes([0]).decode('utf-8'))
			vlen = raw[8]
			if vlen > 55 or vlen == 0:
				return None
			value = raw[9:9+vlen].decode('utf-8')
			return {
				'key': key,
				'value': value
			}
		except Exception as e:
			return None

	def bootloader_is_present (self):
		'''
		Check for the Tock bootloader. Returns `True` if it is present, `False`
		if not, and `None` if unsure.
		'''
		return None

	def get_bootloader_version (self):
		'''
		Return the version string of the bootloader. Should return a value
		like `0.5.0`, or `None` if it is unknown.
		'''
		address = 0x40E
		version_raw = self.read_range(address, 8)
		try:
			return version_raw.decode('utf-8')
		except:
			return None

	def get_apps_start_address (self):
		'''
		Return the address in flash where applications start on this platform.
		This might be set on the board itself, in the command line arguments
		to Tockloader, or just be the default.
		'''

		# Start by checking if we already have the address. This would be if
		# we have already looked it up or we specified it on the command line.
		if self.apps_start_address != None:
			return self.apps_start_address

		# Check if there is an attribute we can use.
		attributes = self.get_all_attributes()
		for attribute in attributes:
			if attribute and attribute['key'] == 'appaddr':
				self.apps_start_address = int(attribute['value'], 0)
				return self.apps_start_address

		# Lastly resort to the default setting
		self.apps_start_address = 0x30000
		return self.apps_start_address

	def determine_current_board (self):
		'''
		Figure out which board we are connected to. Most likely done by reading
		the attributes. Doesn't return anything.
		'''
		return

	def get_board_name (self):
		'''
		Return the name of the board we are connected to.
		'''
		return self.board

	def get_board_arch (self):
		'''
		Return the architecture of the board we are connected to.
		'''
		return self.arch

	def get_page_size (self):
		'''
		Return the size of the page in bytes for the connected board.
		'''
		return self.page_size

	def print_known_boards (self):
		'''
		Display the boards that have settings configured in tockloader.
		'''
		print('Known boards:')
		for board in sorted(self.KNOWN_BOARDS.keys()):
			print('  - {:<20} {}'.format(board, self.KNOWN_BOARDS[board]['description']))

	def run_terminal (self):
		raise TockLoaderException('No terminal mechanism implemented for this host->board communication method.')
