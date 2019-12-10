#!/usr/bin/env python3

'''
### Main command line interface for Tockloader.

Each `tockloader` command is mapped to a function which calls the correct
tockloader class function. This file also handles discovering and reading in TAB
files.
'''

import argparse
import atexit
import binascii
import functools
import glob
import logging
import os
import subprocess
import sys
import textwrap
import time
import urllib.parse

import argcomplete
import crcmod

from . import helpers
from .exceptions import TockLoaderException
from .tab import TAB
from .tockloader import TockLoader
from ._version import __version__

def check_and_run_make (args):
	'''
	Checks for a Makefile, and it it exists runs `make`.
	'''

	if hasattr(args, 'make') and args.make:
		if os.path.isfile('./Makefile'):
			logging.status('Running `make`...')
			p = subprocess.Popen(['make'])
			out, err = p.communicate()
			if p.returncode != 0:
				logging.error('Error running make.')
				sys.exit(1)

def collect_tabs (args):
	'''
	Load in Tock Application Bundle (TAB) files. If none are specified, this
	searches for them in subfolders.

	Also allow downloading apps by name from a server.
	'''

	tab_names = args.tab

	# Check if any tab files were specified. If not, find them based
	# on where this tool is being run.
	if len(tab_names) == 0 or tab_names[0] == '':
		logging.info('No TABs passed to tockloader.')
		logging.status('Searching for TABs in subdirectories.')

		# First check to see if things could be built that haven't been
		if os.path.isfile('./Makefile'):
			p = subprocess.Popen(['make', '-n'], stdout=subprocess.PIPE)
			out, err = p.communicate()
			# Check for the name of the compiler to see if there is work
			# to be done
			if 'arm-none-eabi-gcc' in out.decode('utf-8'):
				logging.warning('Warning! There are uncompiled changes!')
				logging.warning('You may want to run `make` before loading the application.')

		# Search for ".tab" files
		tab_names = glob.glob('./**/*.tab', recursive=True)
		if len(tab_names) == 0:
			raise TockLoaderException('No TAB files found.')

		logging.info('Using: {}'.format(tab_names))

	# Concatenate the binaries.
	tabs = []
	for tab_name in tab_names:
		# Check if this is a TAB locally, or if we should check for it
		# on a remote hosting server.
		if not urllib.parse.urlparse(tab_name).scheme and not os.path.exists(tab_name):
			logging.info('Could not find TAB named "{}" locally.'.format(tab_name))
			response = helpers.menu(['No', 'Yes'],
				return_type='index',
				prompt='Would you like to check the online TAB repository for that app? ')
			if response == 0:
				# User said no, skip this tab_name.
				continue
			else:
				# User said yes, create that URL and try to load the TAB.
				tab_name = 'https://www.tockos.org/assets/tabs/{}.tab'.format(tab_name)

		try:
			tabs.append(TAB(tab_name, args))
		except Exception as e:
			if args.debug:
				logging.debug('Exception: {}'.format(e))
			logging.error('Error opening and reading "{}"'.format(tab_name))

	return tabs


def command_listen (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)
	tock_loader.run_terminal()


def command_list (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)
	tock_loader.list_apps(args.verbose, args.quiet)


def command_install (args):
	check_and_run_make(args)

	# Load in all TABs
	tabs = collect_tabs(args)

	# Install the apps on the board
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	# Figure out how we want to do updates
	replace = 'yes'
	if args.no_replace:
		replace = 'no'

	logging.status('Installing app{} on the board...'.format(helpers.plural(len(tabs))))
	tock_loader.install(tabs, replace=replace, erase=args.erase, sticky=args.sticky)


def command_update (args):
	check_and_run_make(args)
	tabs = collect_tabs(args)

	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Updating application{} on the board...'.format(helpers.plural(len(tabs))))
	tock_loader.install(tabs, replace='only')


def command_uninstall (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	if len(args.name) != 0:
		logging.status('Removing app(s) {} from board...'.format(', '.join(args.name)))
	else:
		logging.status('Preparing to uninstall apps...')
	tock_loader.uninstall_app(args.name, args.force)


def command_erase_apps (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Removing apps...')
	tock_loader.erase_apps(args.force)


def command_enable_app (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Enabling apps...')
	tock_loader.set_flag(args.name, 'enable', True)


def command_disable_app (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Disabling apps...')
	tock_loader.set_flag(args.name, 'enable', False)


def command_sticky_app (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Making apps sticky...')
	tock_loader.set_flag(args.name, 'sticky', True)


def command_unsticky_app (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Making apps no longer sticky...')
	tock_loader.set_flag(args.name, 'sticky', False)


def command_flash (args):
	check_and_run_make(args)

	# Load in all binaries
	binary = bytes()
	for binary_name in args.binary:
		# check that file isn't a `.hex` file
		if binary_name.endswith('.hex'):
			exception_string = 'Error: Cannot flash ".hex" files.'
			exception_string += ' Likely you meant to use a ".bin" file but used an intel hex file by accident.'
			raise TockLoaderException(exception_string)

		# add contents to binary
		with open(binary_name, 'rb') as f:
			binary += f.read()

	# Flash the binary to the chip
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Flashing binar(y|ies) to board...')
	tock_loader.flash_binary(binary, args.address)


def command_read (args):
	# Read the correct flash from the chip
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Reading flash from the board...')
	tock_loader.read_flash(args.address, args.length)


def command_list_attributes (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Listing attributes...')
	tock_loader.list_attributes()


def command_set_attribute (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Setting attribute...')
	tock_loader.set_attribute(args.key, args.value)


def command_remove_attribute (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Removing attribute...')
	tock_loader.remove_attribute(args.key)


def command_info (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	print('tockloader version: {}'.format(__version__))
	logging.status('Showing all properties of the board...')
	tock_loader.info()


def command_inspect_tab (args):
	tabs = collect_tabs(args)

	if len(tabs) == 0:
		raise TockLoaderException('No TABs found to inspect')

	logging.status('Inspecting TABs...')
	for tab in tabs:
		print(tab)

		# If the user asked for the crt0 header, display that for each
		# architecture.
		if args.crt0_header:
			print('  crt0 header')
			archs = tab.get_supported_architectures()
			for arch in archs:
				print('    {}'.format(arch))
				print(textwrap.indent(tab.get_crt0_header_str(arch), '      '))

		print('')


def command_dump_flash_page (args):
	tock_loader = TockLoader(args)
	tock_loader.open(args)

	logging.status('Getting page of flash...')
	tock_loader.dump_flash_page(args.page)


def command_list_known_boards (args):
	tock_loader = TockLoader(args)
	tock_loader.print_known_boards()

################################################################################
## Setup and parse command line arguments
################################################################################

def main ():
	'''
	Read in command line arguments and call the correct command function.
	'''

	# Cleanup any title the program may set
	atexit.register(helpers.set_terminal_title, '')

	# Setup logging for displaying background information to the user.
	logging.basicConfig(style='{', format='[{levelname:<7}] {message}', level=logging.DEBUG)
	# Add a custom status level for logging what tockloader is doing.
	logging.addLevelName(25, 'STATUS')
	logging.Logger.status = functools.partialmethod(logging.Logger.log, 25)
	logging.status = functools.partial(logging.log, 25)

	# Create a common parent parser for arguments shared by all subparsers
	parent = argparse.ArgumentParser(add_help=False)

	# All commands need a serial port to talk to the board
	parent.add_argument('--port', '-p', '--device', '-d',
		help='The serial port or device name to use',
		metavar='STR')

	parent.add_argument('--debug',
		action='store_true',
		help='Print additional debugging information')

	parent.add_argument('--version',
		action='version',
		version=__version__,
		help='Print Tockloader version and exit')

	# Get the list of arguments before any command
	before_command_args = parent.parse_known_args()

	# The top-level parser object
	parser = argparse.ArgumentParser(parents=[parent])

	# Parser for all app related commands
	parent_apps = argparse.ArgumentParser(add_help=False)
	parent_apps.add_argument('--app-address', '-a',
		help='Address where apps are located',
		type=lambda x: int(x, 0))
	parent_apps.add_argument('--force',
		help='Allow apps on boards that are not listed as compatible',
		action='store_true')

	# Parser for most commands
	parent_jtag = argparse.ArgumentParser(add_help=False)
	parent_jtag.add_argument('--jtag',
		action='store_true',
		help='Use JTAG and JLinkExe to flash. Deprecated. Use --jlink instead.')
	parent_jtag.add_argument('--jlink',
		action='store_true',
		help='Use JLinkExe to flash.')
	parent_jtag.add_argument('--openocd',
		action='store_true',
		help='Use OpenOCD to flash.')
	parent_jtag.add_argument('--bossa',
		action='store_true',
		help='Use BOSSA to program the board.')
	parent_jtag.add_argument('--jtag-device',
		default='cortex-m0',
		help='The device type to pass to JLinkExe. Useful for initial commissioning. Deprecated. Use --jlink-device instead.')
	parent_jtag.add_argument('--jlink-device',
		default='cortex-m0',
		help='The device type to pass to JLinkExe. Useful for initial commissioning.')
	parent_jtag.add_argument('--jlink-speed',
		default=1200,
		help='The JLink speed to pass to JLinkExe.')
	parent_jtag.add_argument('--jlink-if',
		default='swd',
		help='The interface type to pass to JLinkExe.')
	parent_jtag.add_argument('--openocd-board',
		default=None,
		help='The cfg file in OpenOCD `board` folder.')
	parent_jtag.add_argument('--openocd-options',
		default=[],
		help='Tockloader-specific flags to direct how Tockloader uses OpenOCD.',
		nargs='*')
	parent_jtag.add_argument('--openocd-commands',
		default={},
		type=lambda kv: kv.split('=', 1),
		action=helpers.ListToDictAction,
		help='Directly specify which OpenOCD commands to use for "program", "read", or "erase" actions. Example: "program=flash write_image erase {{binary}} {address:#x};verify_image {{binary}} {address:#x};"',
		nargs='*')
	parent_jtag.add_argument('--board',
		default=None,
		help='Explicitly specify the board that is being targeted.')
	parent_jtag.add_argument('--arch',
		default=None,
		help='Explicitly specify the architecture of the board that is being targeted.')
	parent_jtag.add_argument('--page-size',
		default=0,
		type=int,
		help='Explicitly specify how many bytes in a flash page.')
	parent_jtag.add_argument('--baud-rate',
		default=115200,
		type=int,
		help='If using serial, set the target baud rate.')
	parent_jtag.add_argument('--no-bootloader-entry',
		action='store_true',
		help='Tell Tockloader to assume the bootloader is already active.')

	# Support multiple commands for this tool
	subparser = parser.add_subparsers(
		title='Commands',
		metavar='')

	listen = subparser.add_parser('listen',
		parents=[parent],
		help='Open a terminal to receive UART data')
	listen.add_argument('--wait-to-listen',
		help='Wait until contacted on server socket to actually listen',
		action='store_true')
	listen.add_argument('--timestamp',
		help='Prepend output with a timestamp',
		action='store_true')
	listen.add_argument('--count',
		help='Prepend output with a message counter',
		action='store_true')
	listen.add_argument('--jlink',
		action='store_true',
		help='Use Segger RTT to listen.')
	listen.add_argument('--board',
		default=None,
		help='Specify the board that is being read from. Only used with --jlink.')
	listen.add_argument('--jlink-device',
		default=None,
		help='The device type to pass to JLinkExe. Only used with --jlink.')
	listen.add_argument('--jlink-speed',
		default=1200,
		help='The JLink speed to pass to JLinkExe. Only used with --jlink.')
	listen.add_argument('--jlink-if',
		default='swd',
		help='The interface type to pass to JLinkExe. Only used with --jlink.')
	listen.set_defaults(func=command_listen)

	listcmd = subparser.add_parser('list',
		parents=[parent, parent_apps, parent_jtag],
		help='List the apps installed on the board')
	listcmd.set_defaults(func=command_list)
	listcmd.add_argument('--verbose', '-v',
		help='Print more information',
		action='store_true')
	listcmd.add_argument('--quiet', '-q',
		help='Print just a list of application names',
		action='store_true')

	install = subparser.add_parser('install',
		parents=[parent, parent_apps, parent_jtag],
		help='Install apps on the board')
	install.set_defaults(func=command_install)
	install.add_argument('tab',
		help='The TAB or TABs to install',
		nargs='*')
	install.add_argument('--no-replace',
		help='Install apps again even if they are already there',
		action='store_true')
	install.add_argument('--make',
		help='Run `make` before loading an application',
		action='store_true')
	install.add_argument('--erase',
		help='Erase all existing apps before installing.',
		action='store_true')
	install.add_argument('--sticky',
		help='Make the installed app(s) sticky.',
		action='store_true')

	update = subparser.add_parser('update',
		parents=[parent, parent_apps, parent_jtag],
		help='Update an existing app with this version')
	update.set_defaults(func=command_update)
	update.add_argument('tab',
		help='The TAB or TABs to replace',
		nargs='*')

	uninstall = subparser.add_parser('uninstall',
		parents=[parent, parent_apps, parent_jtag],
		help='Remove an already flashed app')
	uninstall.set_defaults(func=command_uninstall)
	uninstall.add_argument('name',
		help='The name of the app(s) to remove',
		nargs='*')

	eraseapps = subparser.add_parser('erase-apps',
		parents=[parent, parent_apps, parent_jtag],
		help='Delete apps from the board')
	eraseapps.set_defaults(func=command_erase_apps)

	enableapp = subparser.add_parser('enable-app',
		parents=[parent, parent_apps, parent_jtag],
		help='Enable an app so the kernel runs it')
	enableapp.set_defaults(func=command_enable_app)
	enableapp.add_argument('name',
		help='The name of the app(s) to enable',
		nargs='*')

	disableapp = subparser.add_parser('disable-app',
		parents=[parent, parent_apps, parent_jtag],
		help='Disable an app so it will not be started')
	disableapp.set_defaults(func=command_disable_app)
	disableapp.add_argument('name',
		help='The name of the app(s) to disable',
		nargs='*')

	stickyapp = subparser.add_parser('sticky-app',
		parents=[parent, parent_apps, parent_jtag],
		help='Make an app sticky so it is hard to erase')
	stickyapp.set_defaults(func=command_sticky_app)
	stickyapp.add_argument('name',
		help='The name of the app(s) to sticky',
		nargs='*')

	unstickyapp = subparser.add_parser('unsticky-app',
		parents=[parent, parent_apps, parent_jtag],
		help='Make an app unsticky (the normal setting)')
	unstickyapp.set_defaults(func=command_unsticky_app)
	unstickyapp.add_argument('name',
		help='The name of the app(s) to unsticky',
		nargs='*')

	flash = subparser.add_parser('flash',
		parents=[parent, parent_jtag],
		help='Flash binaries to the chip')
	flash.set_defaults(func=command_flash)
	flash.add_argument('binary',
		help='The binary file or files to flash to the chip',
		nargs='+')
	flash.add_argument('--address', '-a',
		help='Address to flash the binary at',
		type=lambda x: int(x, 0),
		default=0x30000)

	flash = subparser.add_parser('read',
		parents=[parent, parent_jtag],
		help='Read arbitrary flash memory')
	flash.set_defaults(func=command_read)
	flash.add_argument('--address', '-a',
		help='Address to read from',
		type=lambda x: int(x, 0),
		default=0x30000)
	flash.add_argument('--length', '-l',
		help='Number of bytes to read',
		type=lambda x: int(x, 0),
		default=512)

	listattributes = subparser.add_parser('list-attributes',
		parents=[parent, parent_jtag],
		help='List attributes stored on the board')
	listattributes.set_defaults(func=command_list_attributes)

	setattribute = subparser.add_parser('set-attribute',
		parents=[parent, parent_jtag],
		help='Store attribute on the board')
	setattribute.set_defaults(func=command_set_attribute)
	setattribute.add_argument('key',
		help='Attribute key')
	setattribute.add_argument('value',
		help='Attribute value')

	removeattribute = subparser.add_parser('remove-attribute',
		parents=[parent, parent_jtag],
		help='Remove attribute from the board')
	removeattribute.set_defaults(func=command_remove_attribute)
	removeattribute.add_argument('key',
		help='Attribute key')

	info = subparser.add_parser('info',
		parents=[parent, parent_apps, parent_jtag],
		help='Verbose information about the connected board')
	info.set_defaults(func=command_info)

	inspect_tab = subparser.add_parser('inspect-tab',
		parents=[parent],
		help='Get details about a TAB')
	inspect_tab.set_defaults(func=command_inspect_tab)
	inspect_tab.add_argument('--crt0-header',
		help='Dump crt0 header as well',
		action='store_true')
	inspect_tab.add_argument('tab',
		help='The TAB or TABs to inspect',
		nargs='*')

	dump_flash_page = subparser.add_parser('dump-flash-page',
		parents=[parent, parent_jtag],
		help='Read a page of flash from the board')
	dump_flash_page.set_defaults(func=command_dump_flash_page)
	dump_flash_page.add_argument('page',
		help='The number of the page to read',
		type=lambda x: int(x, 0))

	list_known_boards = subparser.add_parser('list-known-boards',
		help='List the boards that Tockloader explicitly knows about')
	list_known_boards.set_defaults(func=command_list_known_boards)

	argcomplete.autocomplete(parser)
	args = parser.parse_args()

	# Concat the args before the command with those that were specified
	# after the command. This is a workaround because for some reason python
	# won't parse a set of parent options before the "command" option
	# (or it is getting overwritten).
	for key,value in vars(before_command_args[0]).items():
		if getattr(args, key) != value:
			setattr(args, key, value)

	# Handle deprecated arguments.
	# --jtag is now --jlink. If --jtag was passed copy it to --jlink.
	if hasattr(args, 'jtag') and args.jtag:
		logging.warning('Deprecation Notice! --jtag has been replaced with --jlink.')
		setattr(args, 'jlink', args.jtag)
	if hasattr(args, 'jtag_device') and args.jtag_device != 'cortex-m0':
		setattr(args, 'jlink_device', args.jtag_device)

	if hasattr(args, 'func'):
		try:
			args.func(args)
		except TockLoaderException as e:
			logging.error(e)
			sys.exit(1)
	else:
		logging.error('Missing Command.\n')
		parser.print_help()
		sys.exit(1)


if __name__ == '__main__':
	main()
