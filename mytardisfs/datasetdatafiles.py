#!/usr/bin/python

# A client process, running as a regular POSIX username (matching a
# MyTardis username) runs this script, which runs as user "mytardis",
# via "sudo -u mytardis", thanks to a rule within /etc/sudoers.

# This script can determine the username which the client script is
# running as, thanks to the SUDO_USER environment variable, so it will
# only provide access to experiment IDs available to that MyTardis
# username.  Currently the username is matched using the auth_provider
# authentication scheme in the MyTardis deployment in mytardis_install_dir
# (defined in [mytardis_install_dir]/tardis/settings.py)

# If the user (os.environ['SUDO_USER']) is a MyTardis superuser,
# then we don't bother checking whether the requested dataset ID
# belongs to the supplied experiment ID, because they will have
# access to that dataset no matter what.

import os
import sys
import getpass
import traceback
import utils

def run():
    if getpass.getuser() != "mytardis" or "SUDO_USER" not in os.environ:
        print "Usage: sudo -u mytardis _datasetdatafiles " + \
            "mytardis_install_dir auth_provider exp_id dataset_id"
        sys.exit(1)

    if len(sys.argv) < 5:
        print "Usage: sudo -u mytardis _datasetdatafiles " + \
            "mytardis_install_dir auth_provider exp_id dataset_id"
        sys.exit(1)

    _mytardis_install_dir = sys.argv[1].strip('"')
    _auth_provider = sys.argv[2]

    utils.setup_mytardis_paths(_mytardis_install_dir)

    from tardis.tardis_portal.models import Dataset, Dataset_File, Experiment
    from tardis.tardis_portal.models import UserAuthentication
    from django.core.exceptions import ObjectDoesNotExist

    _experiment_id = int(sys.argv[3])
    _dataset_id = int(sys.argv[4])

    found_user = False
    mytardis_user = None
    exp_public = False
    exp_owned_or_shared = False
    staff_or_superuser = False
    found_dataset_in_experiment = False
    try:
        mytardis_user = utils.get_user(os.environ['SUDO_USER'], _auth_provider)
        # logger.debug("Primary MyTardis username: " + mytardis_user.username)
        # print "Primary MyTardis username: " + mytardis_user.username
        found_user = True
        staff_or_superuser = mytardis_user.is_staff or \
            mytardis_user.is_superuser
        if not staff_or_superuser:
            exp = Experiment.objects.get(id=_experiment_id)
            exp_public = Experiment \
                .public_access_implies_distribution(exp.public_access)
            exps_owned_and_shared = Experiment.safe \
                .owned_and_shared(mytardis_user)
            exp_owned_or_shared = exps_owned_and_shared \
                .filter(id=_experiment_id).exists()
            for dataset in exp.datasets.all():
                if dataset.id == _dataset_id:
                    found_dataset_in_experiment = True
                    break
        if staff_or_superuser or (found_dataset_in_experiment and
                                  (exp_public or exp_owned_or_shared)):
            dfs = Dataset_File.objects.filter(dataset__id=_dataset_id)
            df_list = []
            for df in dfs:
                df_fields = dict(id=df.id, directory=df.directory,
                                 created_time=str(df.created_time),
                                 modification_time=str(df.modification_time),
                                 filename=df.filename, size=df.size)
                df_list.append(df_fields)
            print str(df_list)
        elif not found_dataset_in_experiment:
            print "Data set (ID %s) does not belong to experiment (ID %s)." % \
                (str(_dataset_id), str(_experiment_id))
        else:
            print "Access to data set %s denied for user %s." % \
                (str(_dataset_id), os.environ['SUDO_USER'])
    except ObjectDoesNotExist:
        print traceback.format_exc()
        # print "User " + os.environ['SUDO_USER'] + " \
        #    was not found in MyTardis."
    except:
        print traceback.format_exc()
