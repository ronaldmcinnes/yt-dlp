"""
User-defined download profiles

A download profile is a named set of command-line options stored in a JSON file.
Profiles are translated into ordinary option strings, so they are processed by the
same parser and option pipeline as configuration files and the command line.

NOTE: This is a local modification and is not part of upstream yt-dlp
"""

import collections
import json
import os
import re
import shlex

from .utils import (
    expand_path,
    get_system_config_dirs,
    get_user_config_dirs,
    render_table,
)

PROFILE_FILE_NAME = 'profiles.json'

# These options control how the configuration and profiles themselves are located,
# which is decided before any profile is applied
_DISALLOWED_OPTIONS = {
    '--help', '--version',
    '--ignore-config', '--no-config', '--config-locations', '--no-config-locations',
    '--profile', '--no-profile', '--profile-file', '--list-profiles', '--show-profile',
}

Profile = collections.namedtuple('Profile', ('name', 'options', 'args'))


class ProfileError(ValueError):
    pass


def get_profile_file_locations(package_name='yt-dlp'):
    """Default locations of the profile file, in order of preference"""
    for config_dir in (*get_user_config_dirs(package_name), *get_system_config_dirs(package_name)):
        yield os.path.join(config_dir, PROFILE_FILE_NAME)


def find_profile_file(path=None):
    """
    Locate the profile file

    @param path     Explicit path to the file or its containing directory (--profile-file).
                    If None, the default locations are searched
    @returns        Path to an existing profile file
    """
    if path is not None:
        path = expand_path(path)
        if os.path.isdir(path):
            path = os.path.join(path, PROFILE_FILE_NAME)
        if not os.path.isfile(path):
            raise ProfileError(f'profile file "{path}" does not exist')
        return path

    locations = list(get_profile_file_locations())
    path = next(filter(os.path.isfile, locations), None)
    if path is None:
        raise ProfileError(
            'no profile file was found. Use --profile-file or create one of: '
            + ', '.join(f'"{location}"' for location in locations))
    return path


def load_profiles(path, parser):
    """
    Read and validate all profiles in a profile file

    @param parser   The option parser used to validate profile options
    @returns        {name: Profile}, in the order they are defined in the file
    """
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
    except OSError as err:
        raise ProfileError(f'unable to read profile file "{path}": {err}')
    except ValueError as err:
        raise ProfileError(f'unable to parse profile file "{path}": {err}')

    if not isinstance(data, dict) or not isinstance(data.get('profiles'), dict):
        raise ProfileError(f'profile file "{path}" must contain a JSON object with a "profiles" object')
    unknown_keys = data.keys() - {'profiles'}
    if unknown_keys:
        raise ProfileError(f'profile file "{path}" has unknown top-level keys: {", ".join(sorted(unknown_keys))}')

    return {
        name: Profile(name, options, profile_to_args(name, options, parser))
        for name, options in data['profiles'].items()
    }


def load_profile_file(path, parser):
    """
    Locate and load the profile file

    @param path     Value of --profile-file, or None to use the default locations
    @returns        (path, {name: Profile})
    """
    path = find_profile_file(path)
    return path, load_profiles(path, parser)


def resolve_profile(name, path, parser):
    """
    Locate the profile file and select a profile from it

    @param path     Value of --profile-file, or None to use the default locations
    @returns        (path, Profile)
    """
    path, profiles = load_profile_file(path, parser)
    if name not in profiles:
        raise ProfileError(
            f'profile "{name}" is not defined in "{path}". '
            f'Available profiles: {", ".join(profiles) or "none"}')
    return path, profiles[name]


def profile_to_args(name, options, parser):
    """Translate the options of a profile into command-line arguments"""
    if not re.fullmatch(r'[\w-]+', name):
        raise ProfileError(f'invalid profile name "{name}"; it may only contain letters, digits, "_" and "-"')
    if not isinstance(options, dict):
        raise ProfileError(f'profile "{name}" must be a JSON object of options')

    args = []
    for key, value in options.items():
        try:
            args.extend(_option_to_args(key, value, parser))
        except ProfileError as err:
            raise ProfileError(f'profile "{name}": {err}')
    return args


def _option_to_args(key, value, parser):
    opt_str = f'--{key.replace("_", "-")}'
    # get_option does exact matching; the parser itself would also accept abbreviations
    option = parser.get_option(opt_str)
    if option is None:
        raise ProfileError(f'unsupported option "{key}"')
    if opt_str in _DISALLOWED_OPTIONS:
        raise ProfileError(f'option "{key}" cannot be used in a profile')

    if not option.takes_value():
        if not isinstance(value, bool):
            raise ProfileError(f'option "{key}" is a switch and must be true or false, not {json.dumps(value)}')
        if value:
            return [opt_str]
        negation = _find_negation(opt_str, option, parser)
        if negation is None:
            raise ProfileError(f'option "{key}" cannot be set to false; remove it from the profile instead')
        return [negation]

    if option.nargs == 1:
        groups = [[item] for item in value] if isinstance(value, list) else [[value]]
    else:
        groups = value if isinstance(value, list) and all(isinstance(item, list) for item in value) else [value]

    if not groups:
        raise ProfileError(f'option "{key}" must not be an empty list')
    args = []
    for group in groups:
        if not isinstance(group, list) or len(group) != option.nargs or not all(map(_is_scalar, group)):
            raise ProfileError(
                f'option "{key}" expects '
                + ('a string or number, or a list of them' if option.nargs == 1
                   else f'a list of {option.nargs} strings or numbers, or a list of such lists')
                + f', not {json.dumps(value)}')
        args.extend((opt_str, *map(str, group)))
    return args


def _is_scalar(value):
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _find_negation(opt_str, option, parser):
    name = opt_str[2:]
    candidates = [f'--no-{name}']
    if name.startswith('no-'):
        candidates.append(f'--{name[3:]}')
    for candidate in candidates:
        negation = parser.get_option(candidate)
        if negation and negation.dest == option.dest and not negation.takes_value():
            return candidate
    return None


def format_profile_list(path, profiles):
    if not profiles:
        return f'No profiles are defined in "{path}"\n'
    return '\n'.join((
        f'Available profiles in "{path}":',
        render_table(['Profile', 'Options'], [[name, shlex.join(p.args)] for name, p in profiles.items()]),
    )) + '\n'


def format_profile(path, profile):
    return '\n'.join((
        f'Profile "{profile.name}" from "{path}":',
        *(f'  {key}: {json.dumps(value, ensure_ascii=False)}' for key, value in profile.options.items()),
        f'Equivalent options: {shlex.join(profile.args)}',
    )) + '\n'
