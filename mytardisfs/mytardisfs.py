#!/usr/bin/env python
# -*- coding: utf-8 -*-
# By James Wettenhall <http://github.com/wettenhj>

# mytardisfs.py:
#
#     Can be installed as "/usr/local/bin/mytardisfs" by running:
#     "sudo python setup.py install" from mytardisfs.py's parent dir.
#
#     Allows a POSIX user on a MyTardis server to access their MyTardis
#     data as a FUSE virtual filesystem in ~/MyTardis/

# Usage:
#          mkdir ~/MyTardis
#   Mount: mytardisfs ~/MyTardis -f -o direct_io 1>stdout.log 2>stderr.log &
#      or: mytardisftpd
# Unmount: fusermount -uz ~/MyTardis

# See /etc/mytardisfs.cnf (installed by sudo python setup.py install)

# Requires FUSE:
#     sudo apt-get install fuse
# FUSE devel libraries may be needed to build fuse-python:
#     sudo apt-get install libfuse-dev
# pkg-config may be needed to build fuse-python:
#     sudo apt-get install pkg-config

# pip and setuptools may be needed to install Python packages:
#     sudo apt-get install python-pip

# The following Python packages should automatically be installed
# by running "sudo python setup.py install" from the mytardisfs/ dir:
#     fuse-python:  sudo pip install fuse-python
#     dateutil:     sudo pip install python-dateutil
#     requests:     sudo pip install requests
#     ConfigParser: sudo pip install ConfigParser

# To Do: Make sure file/directory names are legal, e.g. they shouldn't
# contain the '/' character. Grischa suggests replacing '/' with '-'.

# To Do: nlink is not correct for dataset directories containing
# subdirectories (but not many datasets actually contain subdirectories
# and most (if not all) file browsing clients will still work OK.

# To Do: Provide a way to do simple user mapping, e.g. mapping a
# POSIX username of jsmith to a MyTardis username of jsmith@example.org

import fuse
import stat
import time
import requests
import os
import sys
import getpass
import subprocess
import logging
import traceback
import threading
import ast
import errno
from datafiledescriptor import MyTardisDatafileDescriptor
import dateutil.parser
from datetime import datetime
import getopt
import ConfigParser
from __init__ import __version__

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# We don't really want to log to STDOUT.  We assume that this script
# will be called with STDOUT redirected to a file.
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO)
log_format_string = \
    '%(asctime)s - %(name)s - %(module)s - %(funcName)s - ' + \
    '%(lineno)d - %(levelname)s - %(message)s'
stream_handler.setFormatter(logging.Formatter(log_format_string))
logger.addHandler(stream_handler)

if len(sys.argv) < 2:
    print "Missing mount point"
    print "See `mytardisfs -h' for usage"
    sys.exit(1)

MYTARDISFS_CNF_FILES = ['/etc/mytardisfs.cnf', '/usr/local/etc/mytardisfs.cnf',
                        os.path.join(os.path.expanduser('~'),
                                     '.mytardisfs.cnf')]

logger.info("Looking for MyTardisFS config settings in:\n  " +
            str(MYTARDISFS_CNF_FILES))
mytardisfs_config = ConfigParser.SafeConfigParser(allow_no_value=True)
for cnf_file in MYTARDISFS_CNF_FILES:
    if os.path.exists(cnf_file):
        with open(cnf_file, 'r') as cnf_file_object:
            mytardisfs_config.readfp(cnf_file_object)

# For now, everything in the config will go under one heading: [mytardisfs]
_default_config_file_section = "mytardisfs"

# Set some default values, which will be overwritten by
# the settings in /etc/mytardisfs.cnf:
_mytardis_install_dir = "/opt/mytardis/current"
_mytardis_url = "http://localhost"
_auth_provider = "localdb"
_experiments_list_cache_time_seconds = 30
_experiment_datasets_cache_time_seconds = 30
_dataset_datafiles_cache_time_seconds = 30
_default_directory_size = 4096
_use_api_for_dataset_datafiles = False

if mytardisfs_config.has_section(_default_config_file_section):
    for key, val in mytardisfs_config.items(_default_config_file_section):
        if key == 'mytardis_install_dir':
            _mytardis_install_dir = val
        if key == 'mytardis_url':
            _mytardis_url = val
        if key == 'auth_provider':
            _auth_provider = val
        if key == 'experiments_list_cache_time_seconds':
            _experiments_list_cache_time_seconds = int(val)
        if key == 'experiment_datasets_cache_time_seconds':
            _experiment_datasets_cache_time_seconds = int(val)
        if key == 'dataset_datafiles_cache_time_seconds':
            _dataset_datafiles_cache_time_seconds = int(val)
        if key == 'default_directory_size':
            _default_directory_size = int(val)
        if key == 'use_api_for_dataset_datafiles':
            _use_api_for_dataset_datafiles = (val == 'True')

logger.info("mytardis_install_dir: " + _mytardis_install_dir)
logger.info("mytardis_url: " + _mytardis_url)
logger.info("auth_provider: " + _auth_provider)
logger.info("experiments_list_cache_time_seconds: " +
            str(_experiments_list_cache_time_seconds))
logger.info("experiment_datasets_cache_time_seconds: " +
            str(_experiment_datasets_cache_time_seconds))
logger.info("dataset_datafiles_cache_time_seconds: " +
            str(_dataset_datafiles_cache_time_seconds))
logger.info("default_directory_size: " +
            str(_default_directory_size))
logger.info("use_api_for_dataset_datafiles: " +
            str(_use_api_for_dataset_datafiles))

if sys.argv[1].startswith("-"):
    argv = sys.argv[1:]
else:
    argv = sys.argv[2:]

try:
    opts, args = getopt.getopt(argv, "hvfdsl:o:",
                               ["help", "version", "loglevel="])
except getopt.GetoptError:
    print "Usage: mytardisfs mountpoint [options]"
    sys.exit(1)

for opt, arg in opts:
    if opt == '-h' or opt == '--help':
        print ""
        print "Usage: mytardisfs mountpoint [options]"
        print "  e.g. mytardisfs ~/MyTardis -f -o direct_io"
        print """
General options:
    -h   --help            print help
    -v   --version         print version

MyTardisFS options:
    -l   --loglevel=LEVEL  set log level to ERROR, WARNING, INFO or DEBUG

FUSE options:
    -d   -o debug          enable debug output (implies -f)
    -f                     foreground operation
    -s                     disable multi-threaded operation

    -o allow_other         allow access to other users
    -o allow_root          allow access to root
    -o nonempty            allow mounts over non-empty file/dir
    -o default_permissions enable permission checking by kernel
    -o fsname=NAME         set filesystem name
    -o subtype=NAME        set filesystem type
    -o large_read          issue large read requests (2.4 only)
    -o max_read=N          set maximum size of read requests

    -o hard_remove         immediate removal (don't hide files)
    -o use_ino             let filesystem set inode numbers
    -o readdir_ino         try to fill in d_ino in readdir
    -o direct_io           use direct I/O
    -o kernel_cache        cache files in kernel
    -o [no]auto_cache      enable caching based on modification times (off)
    -o umask=M             set file permissions (octal)
    -o uid=N               set file owner
    -o gid=N               set file group
    -o entry_timeout=T     cache timeout for names (1.0s)
    -o negative_timeout=T  cache timeout for deleted names (0.0s)
    -o attr_timeout=T      cache timeout for attributes (1.0s)
    -o ac_attr_timeout=T   auto cache timeout for attributes (attr_timeout)
    -o intr                allow requests to be interrupted
    -o intr_signal=NUM     signal to send on interrupt (10)
    -o modules=M1[:M2...]  names of modules to push onto filesystem stack

    -o max_write=N         set maximum size of write requests
    -o max_readahead=N     set maximum readahead
    -o async_read          perform reads asynchronously (default)
    -o sync_read           perform reads synchronously
    -o atomic_o_trunc      enable atomic open+truncate support
    -o big_writes          enable larger than 4kB writes
    -o no_remote_lock      disable remote file locking
"""

        sys.exit()
    if opt == '-v' or opt == '--version':
        print ""
        print "MyTardisFS version: " + __version__
        print "fuse-python version: " + fuse.__version__
        print "FUSE version: " + \
            subprocess.check_output('ldconfig -v 2>/dev/null | grep fuse',
                                    shell=True)
        sys.exit()
    if opt == '-l' or opt == '--loglevel':
        # Remove this option from sys.argv,
        # because it is not a valid FUSE
        # option, and all command-line options
        # will be passed to FUSE.
        try:
            sys.argv.remove(opt + "=" + arg)
        except:
            try:
                sys.argv.remove(opt + arg)
            except:
                try:
                    sys.argv.remove(opt)
                    sys.argv.remove(arg)
                except:
                    pass
        if arg == 'ERROR':
            logger.setLevel(logging.ERROR)
            stream_handler.setLevel(logging.ERROR)
        elif arg == 'WARNING':
            logger.setLevel(logging.WARNING)
            stream_handler.setLevel(logging.WARNING)
        elif arg == 'INFO':
            logger.setLevel(logging.INFO)
            stream_handler.setLevel(logging.INFO)
        elif arg == 'DEBUG':
            logger.setLevel(logging.DEBUG)
            stream_handler.setLevel(logging.DEBUG)
            # FUSE needs to run in foreground mode (-f) to allow
            # debug-level logging.  You can still use an ampersand
            # at the end of the command to put it in the background.
            # The logs can be redirected to files, as done in mytardisftpd
            if '-f' not in sys.argv:
                sys.argv.append('-f')
        else:
            print "--loglevel should be ERROR, WARNING, INFO or DEBUG."
            sys.exit(1)

# Checking again, after possible removing "--loglevel"
if len(sys.argv) < 2:
    print "Missing mount point"
    print "See `mytardisfs -h' for usage"
    sys.exit(1)

fuse_mount_dir = os.path.expanduser(sys.argv[1])
if not os.path.exists(fuse_mount_dir):
    os.makedirs(fuse_mount_dir)

mytardis_username = getpass.getuser()
proc = subprocess.Popen(["sudo", "-n", "-u", "mytardis", "_myapikey",
                         _mytardis_install_dir, _auth_provider],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
stdout, stderr = proc.communicate()
if proc.returncode != 0:
    message = "Attempting to retrieve your MyTardis API key " + \
        "as the 'mytardis' user failed.\n\n" + \
        "Please ensure that you have read the instructions " + \
        "in:\n\nhttps://github.com/monash-merc/mytardisfs/" + \
        "blob/master/README.md\n\nfor configuring /etc/sudoers\n\n" + \
        "You might need to run:\n\n" + \
        "  " + os.path.join(_mytardis_install_dir, "bin", "django") + \
        " backfill_api_keys\n\n" + \
        "as the 'mytardis' user to generate an API key for your " + \
        "MyTardis user account.\n"
    logger.error(message)
    sys.stderr.write(message)
    sys.stderr.write("\n")
    sys.stderr.write(stderr)
    sys.exit(1)
myapikey_stdout = stdout.strip()
mytardis_username = myapikey_stdout.split(' ')[1].split(':')[0]
mytardis_apikey = myapikey_stdout.split(':')[-1]

proc = subprocess.Popen(["id", "-u"], stdout=subprocess.PIPE)
_uid = proc.stdout.read().strip()

proc = subprocess.Popen(["id", "-g"], stdout=subprocess.PIPE)
_gid = proc.stdout.read().strip()

_headers = {'Authorization': 'ApiKey ' + mytardis_username + ":" +
            mytardis_apikey}

LAST_QUERY_TIME = dict()
LAST_QUERY_TIME['experiments'] = datetime.fromtimestamp(0)

fuse.fuse_python_api = (0, 2)

# Timestamps obtained from MyTardis queries will be used
# if available.
# Start-up time of this FUSE process is the default
# timestamp for everything:
_file_default_timestamp = int(time.time())


class DirEntry():
    def __init__(self, file_path, size_in_bytes, is_directory,
                 accessed=_file_default_timestamp,
                 modified=_file_default_timestamp,
                 created=_file_default_timestamp,
                 nlink=0):
        self.file_path = file_path
        self.size_in_bytes = size_in_bytes
        self.is_directory = is_directory
        self.accessed = accessed
        self.modified = modified
        self.created = created
        self.nlink = nlink

        if self.nlink == 0:
            if self.is_directory:
                self.nlink = 2
            else:
                self.nlink = 1

    def get_file_path(self):
        return self.file_path

    def get_size_in_bytes(self):
        return self.size_in_bytes

    def get_is_directory(self):
        return self.is_directory

    def get_accessed(self):
        return self.accessed

    def get_modified(self):
        return self.modified

    def get_created(self):
        return self.created

    def get_nlink(self):
        return self.nlink

# FILES[file_path] = \
#     DirEntry(file_path, size_in_bytes, is_directory,
#              accessed, modified, created, nlink)
FILES = dict()
DATAFILE_IDS = dict()
DATAFILE_SIZES = dict()
DATAFILE_FILE_OBJECTS = dict()
DATAFILE_CLOSE_TIMERS = dict()

url = _mytardis_url + "/api/v1/experiment/?format=json&limit=0"
logger.info(url)
response = requests.get(url=url, headers=_headers)
if response.status_code < 200 or response.status_code >= 300:
    logger.info("Response status_code = " + str(response.status_code))
exp_records_json = response.json()
if response.status_code < 200 or response.status_code >= 300:
    logger.info(exp_records_json)
num_exp_records_found = exp_records_json['meta']['total_count']
logger.info(str(num_exp_records_found) +
            " experiment record(s) found for user " + mytardis_username)

cmd = ['sudo', '-n', '-u', 'mytardis',
       '/usr/local/bin/_countexpdatasets',
       _mytardis_install_dir, _auth_provider]
logger.info(str(cmd))
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
stdout, stderr = proc.communicate()
if stderr is not None and stderr != "":
    logger.info(stderr)
try:
    expdatasetcounts = ast.literal_eval(stdout.strip())
except:
    expdatasetcounts = dict()

max_exp_created_time = datetime.fromtimestamp(0)
for exp_record_json in exp_records_json['objects']:
    exp_dir_name = str(exp_record_json['id']) + "-" + \
        exp_record_json['title'].encode('ascii', 'ignore').replace(" ", "_")
    exp_created_time = dateutil.parser.parse(exp_record_json['created_time'])
    if exp_created_time > max_exp_created_time:
        max_exp_created_time = exp_created_time
    exp_created_timestamp = \
        int(time.mktime(exp_created_time.timetuple()))

    nlink = 2
    if exp_record_json['id'] in expdatasetcounts.keys():
        num_datasets = expdatasetcounts[exp_record_json['id']]
        nlink = num_datasets + 2

    exp_dir_entry = \
        DirEntry(file_path='/' + exp_dir_name,
                 size_in_bytes=_default_directory_size,
                 is_directory=True,
                 accessed=exp_created_timestamp,
                 modified=exp_created_timestamp,
                 created=exp_created_timestamp,
                 nlink=nlink)
    FILES[exp_dir_entry.get_file_path()] = exp_dir_entry

max_exp_created_timestamp = \
    int(time.mktime(max_exp_created_time.timetuple()))
root_dir_entry = \
    DirEntry(file_path='/',
             size_in_bytes=_default_directory_size,
             is_directory=True,
             accessed=max_exp_created_timestamp,
             modified=max_exp_created_timestamp,
             created=max_exp_created_timestamp,
             nlink=int(num_exp_records_found)+2)
FILES[root_dir_entry.get_file_path()] = root_dir_entry
# logger.info("FILES['/'] = " + str(FILES['/']))

LAST_QUERY_TIME['experiments'] = datetime.now()


def file_array_to_list(files):
    # Files need to be returned in this format:
    #     [('file1', 15, False), ('file2', 15, False),
    #      ('directory', 15, True)]

    l = list()
    for key, dir_entry in files.iteritems():
        l.append((file_from_key(key), dir_entry.get_size_in_bytes(),
                  dir_entry.get_is_directory()))
    return l


def file_from_key(key):
    return key.rsplit(os.sep)[-1]


class MyStat(fuse.Stat):
    """
    Convenient class for Stat objects.
    Set up the stat object with appropriate
    values depending on constructor args.
    """
    def __init__(self, dir_entry):
        fuse.Stat.__init__(self)
        if dir_entry.get_is_directory():
            self.st_mode = stat.S_IFDIR | stat.S_IRUSR | stat.S_IXUSR
            if dir_entry.get_nlink() != 0:
                self.st_nlink = dir_entry.get_nlink()
            else:
                # A directory without subdirectories
                # still has "." and ".."
                self.st_nlink = 2
            self.st_size = dir_entry.get_size_in_bytes()
        else:
            self.st_mode = stat.S_IFREG | stat.S_IRUSR
            self.st_nlink = 1
            self.st_size = dir_entry.get_size_in_bytes()
        self.st_atime = dir_entry.get_accessed()
        self.st_mtime = dir_entry.get_modified()
        self.st_ctime = dir_entry.get_created()

        self.st_uid = int(_uid)
        self.st_gid = int(_gid)


class MyFS(fuse.Fuse):
    def __init__(self, *args, **kw):
        fuse.Fuse.__init__(self, *args, **kw)

    def getattr(self, path):
        path = path.rstrip("*")
        if path != "/":
            path = path.rstrip("/")
        logger.debug("^ getattr: path = " + path)

        try:
            return MyStat(FILES[path])
        except KeyError:
            logger.debug("KeyError in getattr for path: " + str(path))
            return -errno.ENOENT

    def getdir(self, path):
        logger.debug('getdir called:', path)
        return file_array_to_list(FILES)

    def readdir(self, path, offset):
        logger.debug("^ readdir: path = \"" + path + "\"")

        for e in '.', '..':
            yield fuse.Direntry(e)

        pathComponents = path.split(os.sep, 3)
        if pathComponents == ['', '']:
            pathComponents = ['']
        if len(pathComponents) > 1 and pathComponents[1] != '':
            exp_dir_name = pathComponents[1]
            experiment_id = exp_dir_name.split("-")[0]
        if len(pathComponents) > 2 and pathComponents[2] != '':
            dataset_dir_name = pathComponents[2]
            dataset_id = dataset_dir_name.split("-")[0]
        # subdirectory is not used in "def readdir". Should it be?
        if len(pathComponents) > 3 and pathComponents[3] != '':
            subdirectory = pathComponents[3]

        if len(pathComponents) == 1:
            time_since_last_experiments_query = datetime.now() - \
                LAST_QUERY_TIME['experiments']
            if time_since_last_experiments_query.seconds > \
                    _experiments_list_cache_time_seconds:
                url = _mytardis_url + "/api/v1/experiment/?format=json&limit=0"
                logger.info(url)
                response = requests.get(url=url, headers=_headers)
                if response.status_code < 200 or response.status_code >= 300:
                    logger.info("Response status_code = " +
                                str(response.status_code))
                exp_records_json = response.json()
		if response.status_code < 200 or response.status_code >= 300:
		    logger.info(exp_records_json)
                num_exp_records_found = exp_records_json['meta']['total_count']
                logger.info(str(num_exp_records_found) +
                            " experiment record(s) found for user " +
                            mytardis_username)

                cmd = ['sudo', '-n', '-u', 'mytardis',
                       '/usr/local/bin/_countexpdatasets',
                       _mytardis_install_dir, _auth_provider]
                logger.info(str(cmd))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
                stdout, stderr = proc.communicate()
                if stderr is not None and stderr != "":
                    logger.info(stderr)
                try:
                    expdatasetcounts = ast.literal_eval(stdout.strip())
                except:
                    expdatasetcounts = {}

                # Doesn't check for deleted experiments,
                # only adds to FILES dictionary.
                max_exp_created_time = datetime.fromtimestamp(0)
                for exp_record_json in exp_records_json['objects']:
                    exp_dir_name = str(exp_record_json['id']) + "-" + \
                        (exp_record_json['title'].encode('ascii', 'ignore')
                            .replace(" ", "_"))
                    exp_created_time = \
                        dateutil.parser.parse(exp_record_json['created_time'])
                    if exp_created_time > max_exp_created_time:
                        max_exp_created_time = exp_created_time
                    exp_created_timestamp = \
                        int(time.mktime(exp_created_time.timetuple()))

                    nlink = 2
                    if exp_record_json['id'] in expdatasetcounts.keys():
                        num_datasets = expdatasetcounts[exp_record_json['id']]
                        nlink = num_datasets + 2

                    exp_dir_entry = \
                        DirEntry(file_path='/'+exp_dir_name,
                                 size_in_bytes=_default_directory_size,
                                 is_directory=True,
                                 accessed=exp_created_timestamp,
                                 modified=exp_created_timestamp,
                                 created=exp_created_timestamp,
                                 nlink=nlink)
                    FILES[exp_dir_entry.get_file_path()] = exp_dir_entry
                max_exp_created_timestamp = \
                    int(time.mktime(max_exp_created_time.timetuple()))
                root_dir_entry = \
                    DirEntry(file_path='/',
                             size_in_bytes=_default_directory_size,
                             is_directory=True,
                             accessed=max_exp_created_timestamp,
                             modified=max_exp_created_timestamp,
                             created=max_exp_created_timestamp,
                             nlink=int(num_exp_records_found)+2)
                FILES[root_dir_entry.get_file_path()] = root_dir_entry
                LAST_QUERY_TIME['experiments'] = datetime.now()

        if len(pathComponents) == 2 and pathComponents[1] != '':
            if experiment_id+'_datasets' not in LAST_QUERY_TIME:
                LAST_QUERY_TIME[experiment_id+'_datasets'] = \
                    datetime.fromtimestamp(0)
            time_since_last_experiment_datasets_query = datetime.now() - \
                LAST_QUERY_TIME[experiment_id+'_datasets']
            if time_since_last_experiment_datasets_query.seconds > \
                    _experiment_datasets_cache_time_seconds:
                url = _mytardis_url + \
                    "/api/v1/dataset/?format=json&limit=0&experiments__id=" + \
                    experiment_id
                logger.info(url)
                response = requests.get(url=url, headers=_headers)
                if response.status_code < 200 or response.status_code >= 300:
                    logger.info("Response status_code = " +
                                str(response.status_code))
                dataset_records_json = response.json()
                if response.status_code < 200 or response.status_code >= 300:
                    logger.info(dataset_records_json)
                num_dataset_records_found = \
                    dataset_records_json['meta']['total_count']
                logger.info(str(num_dataset_records_found) +
                            " dataset record(s) found for exp ID " +
                            experiment_id)

                for dataset_json in dataset_records_json['objects']:
                    dataset_dir_name = str(dataset_json['id']) + "-" + \
                        (dataset_json['description'].encode('ascii', 'ignore')
                            .replace(" ", "_"))
                    dataset_dir_entry = \
                        DirEntry(file_path='/' + exp_dir_name + '/' +
                                 dataset_dir_name,
                                 size_in_bytes=_default_directory_size,
                                 is_directory=True)
                    FILES[dataset_dir_entry.get_file_path()] = \
                        dataset_dir_entry

            LAST_QUERY_TIME[experiment_id+'_datasets'] = datetime.now()

        if len(pathComponents) == 3 and pathComponents[1] != '':
            if dataset_id+'_datafiles' not in LAST_QUERY_TIME:
                LAST_QUERY_TIME[dataset_id+'_datafiles'] = \
                    datetime.fromtimestamp(0)
            time_since_last_dataset_datafiles_query = datetime.now() - \
                LAST_QUERY_TIME[dataset_id+'_datafiles']
            if time_since_last_dataset_datafiles_query.seconds > \
                    _dataset_datafiles_cache_time_seconds:
                dataset_dir_entry = \
                    DirEntry(file_path='/'+exp_dir_name+'/'+dataset_dir_name,
                             size_in_bytes=_default_directory_size,
                             is_directory=True)
                FILES[dataset_dir_entry.get_file_path()] = dataset_dir_entry
                DATAFILE_IDS[dataset_id] = dict()
                DATAFILE_SIZES[dataset_id] = dict()
                DATAFILE_FILE_OBJECTS[dataset_id] = dict()
                DATAFILE_CLOSE_TIMERS[dataset_id] = dict()

                _use_api_for_dataset_datafiles = False

                if _use_api_for_dataset_datafiles:
                    url = _mytardis_url + \
                        "/api/v1/dataset_file/?format=json&limit=0&" + \
                        "dataset__id=" + str(dataset_id)
                    logger.info(url)
                    response = requests.get(url=url, headers=_headers)
                    datafile_records_json = response.json()
                    num_datafile_records_found = \
                        datafile_records_json['meta']['total_count']
                else:
                    cmd = ['sudo', '-n', '-u', 'mytardis',
                           '/usr/local/bin/_datasetdatafiles',
                           _mytardis_install_dir, _auth_provider,
                           experiment_id, dataset_id]
                    logger.info(str(cmd))
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE)
                    stdout, stderr = proc.communicate()
                    if stderr is not None and stderr != "":
                        logger.info(stderr)

                    datafile_dicts_string = stdout.strip()
                    # logger.info("datafile_dicts_string: " +
                    #     datafile_dicts_string)
                    datafile_dicts = ast.literal_eval(datafile_dicts_string)
                    num_datafile_records_found = len(datafile_dicts)

                logger.info(str(num_datafile_records_found) +
                            " datafile record(s) found for dataset ID " +
                            str(dataset_id))

                if _use_api_for_dataset_datafiles:
                    datafile_dicts = datafile_records_json['objects']

                for df in datafile_dicts:
                    # logger.debug("df = " + str(df))
                    datafile_id = df['id']
                    if _use_api_for_dataset_datafiles:
                        df_directory = df['directory'] \
                            .encode('ascii', 'ignore').strip('/')
                    else:
                        df_directory = df['directory']
                        if df_directory is None:
                            df_directory = ""
                        else:
                            df_directory = df_directory \
                                .encode('ascii', 'ignore').strip('/')
                    df_filename = df['filename'] \
                        .encode('ascii', 'ignore')
                    df_size = int(df['size'].encode('ascii', 'ignore'))
                    try:
                        df_created_time_datetime = \
                            dateutil.parser.parse(df['created_time'])
                        df_created_timetuple = \
                            df_created_time_datetime.timetuple()
                        df_created_time = \
                            int(time.mktime(df_created_timetuple))
                    except:
                        logger.debug(traceback.format_exc())
                        df_created_time = _file_default_timestamp
                    try:
                        df_modification_time_datetime = \
                            dateutil.parser.parse(df['modification_time'])
                        df_modification_timetuple = \
                            df_modification_time_datetime.timetuple()
                        df_modification_time = \
                            int(time.mktime(df_modification_timetuple))
                    except:
                        logger.debug(traceback.format_exc())
                        df_modification_time = _file_default_timestamp

                    df_accessed_time = df_modification_time

                    if df_directory != "":
                        # Intermediate subdirectories
                        for i in reversed(range(1,
                                          len(df_directory.split('/')))):
                            intermediate_subdirectory = \
                                df_directory.rsplit('/', i)[0]
                            intermediate_subdir_entry = \
                                DirEntry(file_path='/' + exp_dir_name + '/' +
                                         dataset_dir_name + '/' +
                                         intermediate_subdirectory,
                                         size_in_bytes=_default_directory_size,
                                         is_directory=True,
                                         accessed=df_accessed_time,
                                         modified=df_accessed_time,
                                         created=df_accessed_time)
                            FILES[intermediate_subdir_entry.get_file_path()] = \
                                intermediate_subdir_entry

                        datafile_dir_entry = \
                            DirEntry(file_path='/' + exp_dir_name + '/' +
                                     dataset_dir_name + '/' +
                                     df_directory,
                                     size_in_bytes=_default_directory_size,
                                     is_directory=True,
                                     accessed=df_accessed_time,
                                     modified=df_accessed_time,
                                     created=df_accessed_time)
                        FILES[datafile_dir_entry.get_file_path()] = \
                            datafile_dir_entry

                        datafile_entry = \
                            DirEntry(file_path='/' + exp_dir_name + '/' +
                                     dataset_dir_name + '/' +
                                     df_directory + '/' +
                                     df_filename,
                                     size_in_bytes=df_size,
                                     is_directory=False,
                                     accessed=df_accessed_time,
                                     modified=df_accessed_time,
                                     created=df_accessed_time)
                        FILES[datafile_entry.get_file_path()] = datafile_entry
                    else:
                        datafile_entry = \
                            DirEntry(file_path='/' + exp_dir_name + '/' +
                                     dataset_dir_name + '/' +
                                     df_filename,
                                     size_in_bytes=df_size,
                                     is_directory=False,
                                     accessed=df_accessed_time,
                                     modified=df_accessed_time,
                                     created=df_accessed_time)
                        FILES[datafile_entry.get_file_path()] = datafile_entry
                    if df_directory not in DATAFILE_IDS[dataset_id]:
                        DATAFILE_IDS[dataset_id][df_directory] = dict()
                    DATAFILE_IDS[dataset_id][df_directory][df_filename] \
                        = datafile_id
                    if df_directory not in DATAFILE_SIZES[dataset_id]:
                        DATAFILE_SIZES[dataset_id][df_directory] = dict()
                    dsdict = DATAFILE_SIZES[dataset_id][df_directory]
                    dsdict[df_filename] = df_size
                    if df_directory not in DATAFILE_FILE_OBJECTS[dataset_id]:
                        DATAFILE_FILE_OBJECTS[dataset_id][df_directory] \
                            = dict()
                    dfodict = DATAFILE_FILE_OBJECTS[dataset_id][df_directory]
                    dfodict[df_filename] = None
                    if df_directory not in DATAFILE_CLOSE_TIMERS[dataset_id]:
                        DATAFILE_CLOSE_TIMERS[dataset_id][df_directory] \
                            = dict()
                    dctdict = DATAFILE_CLOSE_TIMERS[dataset_id][df_directory]
                    dctdict[df_filename] = None
            LAST_QUERY_TIME[dataset_id+'_datafiles'] = datetime.now()

        path_depth = path.count('/')
        # FIXME: Iterating through the entire FILES dictionary is inefficient
        for key, val in FILES.iteritems():
            if key == "/":
                continue

            key_depth = key.count('/')

            if path == "/":
                path_depth = 0

            if key.startswith(path) and key_depth == path_depth + 1:
                yield(fuse.Direntry(file_from_key(key)))

    def read(self, path, leng, offset):

        logger.debug("read(...) path = " + path)

        filename = path.rsplit(os.sep)[-1]
        pathComponents = path.split(os.sep, 3)
        if pathComponents == ['', '']:
            pathComponents = ['']
        experiment_id = pathComponents[1].split("-")[0]
        dataset_id = pathComponents[2].split("-")[0]
        if os.sep in pathComponents[3]:
            subdirectory = pathComponents[3].rsplit(os.sep, 1)[0]
        else:
            subdirectory = ""
        logger.debug("read request for %s with length %d and offset %d" %
                     (filename, leng, offset))

        datafile_id = DATAFILE_IDS[dataset_id][subdirectory][filename]

        datafile_size = DATAFILE_SIZES[dataset_id][subdirectory][filename]
        logger.debug("datafile_size is " + str(datafile_size))

        if DATAFILE_FILE_OBJECTS[dataset_id][subdirectory][filename] \
                is not None:
            # Found a file object to reuse,
            # so let's reset the timer for closing the file:
            file_object = \
                DATAFILE_FILE_OBJECTS[dataset_id][subdirectory][filename]
            DATAFILE_CLOSE_TIMERS[dataset_id][subdirectory][filename].cancel()

            def closeFile(fileObj, dictObj, key):
                fileObj.close()
                dictObj[key] = None

            dfodict = DATAFILE_FILE_OBJECTS[dataset_id][subdirectory]
            DATAFILE_CLOSE_TIMERS[dataset_id][subdirectory][filename] \
                = threading.Timer(30.0, closeFile, [file_object, dfodict,
                                                    filename])
            DATAFILE_CLOSE_TIMERS[dataset_id][subdirectory][filename].start()
        else:
            mytardis_datafile_descriptor = MyTardisDatafileDescriptor. \
                get_file_descriptor(_mytardis_install_dir, _auth_provider,
                                    experiment_id, datafile_id)
            file_descriptor = None
            logger.debug("Message: " +
                         mytardis_datafile_descriptor.message)
            if mytardis_datafile_descriptor.file_descriptor is not None:
                file_descriptor = mytardis_datafile_descriptor.file_descriptor
            else:
                logger.info("mytardis_datafile_descriptor.file_descriptor "
                            "is None.")

            file_object = os.fdopen(file_descriptor)
            DATAFILE_FILE_OBJECTS[dataset_id][subdirectory][filename] = \
                file_object

            # Schedule file to be closed in 30 seconds, unless it is used
            # before then, in which case the timer will be reset.

            def closeFile(fileObj, dictObj, key):
                fileObj.close()
                dictObj[key] = None

            dfodict = DATAFILE_FILE_OBJECTS[dataset_id][subdirectory]
            DATAFILE_CLOSE_TIMERS[dataset_id][subdirectory][filename] = \
                threading.Timer(30.0, closeFile, [file_object, dfodict,
                                                  filename])
            DATAFILE_CLOSE_TIMERS[dataset_id][subdirectory][filename].start()

        file_object.seek(offset)
        data = file_object.read(leng)

        return data

if __name__ == '__main__':
    fs = MyFS()
    fs.parse(errex=1)
    fs.main()


def run():
    fs = MyFS()
    fs.parse(errex=1)
    fs.main()
