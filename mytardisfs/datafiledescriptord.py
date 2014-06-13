#!/usr/bin/python

# Socket server program which opens file with elevated privileges, and then
# passes file descriptor to another Python process via an IPC socket.

# A client process, running as a regular LDAP username (matching a MyTardis
# username) starts this server process, which, (running as user "mytardis",
# via "sudo -u mytardis", thanks to a rule within /etc/sudoers), has
# read-access to all datafiles. This server process can determine the username
# which the client script is running as, thanks to the SUDO_USER environment
# variable, so it will only provide access to experiment IDs available to
# that MyTardis username.  Currently the username is matched using the
# "cvl_ldap" authentication scheme in our MyTardis deployment
# (defined in /opt/mytardis/current/tardis/settings.py)

import os
import socket
import fdsend
import sys
import getpass
import traceback
import utils


def run():
    if getpass.getuser() != "mytardis" or "SUDO_USER" not in os.environ:
        print "Usage: sudo -u mytardis _datafiledescriptord " + \
            "mytardis_install_dir auth_provider " + \
            "socket_path exp_id datafile_id"
        sys.exit(1)

    if len(sys.argv) < 6:
        print "Usage: sudo -u mytardis _datafiledescriptord " + \
            "mytardis_install_dir auth_provider " + \
            "socket_path exp_id datafile_id"
        sys.exit(1)

    _mytardis_install_dir = sys.argv[1].strip('"')
    _auth_provider = sys.argv[2]
    _socket_path = sys.argv[3]
    _experiment_id = int(sys.argv[4])
    _datafile_id = int(sys.argv[5])

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.remove(_socket_path)
    except OSError:
        pass
    sock.bind(_socket_path)
    os.chmod(_socket_path, 0666)
    sock.listen(1)
    conn, addr = sock.accept()

    utils.setup_mytardis_paths(_mytardis_install_dir)

    from tardis.tardis_portal.models import Dataset_File, Experiment

    found_user = False
    mytardis_user = None
    exp_public = False
    exp_owned_or_shared = False
    staff_or_superuser = False
    found_datafile_in_experiment = False
    try:
        mytardis_user = utils.get_user(os.environ['SUDO_USER'], _auth_provider)
        # logger.debug("Primary MyTardis username: " + mytardis_user.username)
        found_user = True
        staff_or_superuser = mytardis_user.is_staff or \
            mytardis_user.is_superuser
        if not staff_or_superuser:
            exp = Experiment.objects.get(id=_experiment_id)
            exp_public = Experiment.public_access_implies_distribution(
                exp.public_access)
            experiments_owned_or_shared = Experiment \
                .safe.owned_and_shared(mytardis_user)
            exp_owned_or_shared = experiments_owned_or_shared \
                .filter(id=_experiment_id).exists()
            for df in exp.get_datafiles():
                if df.id == _datafile_id:
                    found_datafile_in_experiment = True
                    break
        df = Dataset_File.objects.get(id=_datafile_id)
        r = df.get_preferred_replica()
        filepath = r.get_absolute_filepath()

        # The following line blocks, waiting for client to start up
        # and send its request:
        file_descriptor_request = conn.recv(1024)
        if staff_or_superuser or (found_datafile_in_experiment and
                                  (exp_public or exp_owned_or_shared)):
            fds = [file(filepath, 'rb')]
            message = "Success"
        elif not found_datafile_in_experiment:
            fds = []
            message = "Datafile (ID %s) does not belong " + \
                "to experiment (ID %s)." % \
                (str(_datafile_id), str(_experiment_id))
        else:
            fds = []
            # message = "Access to datafile %s denied for user %s." %
            # (str(_datafile_id),os.environ['SUDO_USER'])
            message = "Access denied for user " + \
                os.environ['SUDO_USER'] + " " + \
                str(sys.argv)
        fdsend.sendfds(conn, message, fds=fds)
    except ObjectDoesNotExist:
        message = "User " + os.environ['SUDO_USER'] + \
            " was not found in MyTardis."
        fdsend.sendfds(conn, message, fds=[])
    except:
        message = traceback.format_exc()
        fdsend.sendfds(conn, message, fds=[])

    conn.close()

    try:
        os.remove(_socket_path)
    except OSError:
        pass
