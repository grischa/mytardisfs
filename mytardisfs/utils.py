
import os
import sys


def setup_mytardis_paths(mytardis_base_path):
    sys.path.append(mytardis_base_path)
    for egg in os.listdir(os.path.join(mytardis_base_path, "eggs")):
        sys.path.append(os.path.join(mytardis_base_path, "eggs", egg))
    from django.core.management import setup_environ
    from tardis import settings
    setup_environ(settings)

from django.contrib.auth.models import User
from tardis.tardis_portal.models import UserAuthentication


def get_user(sys_user, auth_method='localdb'):
    try:
        userAuth = UserAuthentication.objects.get(
            username=sys_user, authenticationMethod=auth_method)
        return userAuth.userProfile.user
    except UserAuthentication.DoesNotExist:
        pass
    try:
        return User.objects.get(username=sys_user)
    except UserAuthentication.DoesNotExist:
        return None
