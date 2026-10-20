#!/usr/bin/env python3

# Allow direct execution
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import contextlib
import io
import json
import optparse
import tempfile

from test.test_config import set_environ
from yt_dlp import _real_main, parse_options
from yt_dlp.options import create_parser, parseOpts
from yt_dlp.profiles import (
    PROFILE_FILE_NAME,
    ProfileError,
    find_profile_file,
    format_profile,
    format_profile_list,
    get_profile_file_locations,
    load_profile_file,
    load_profiles,
    resolve_profile,
)

PROFILES = {
    'podcast': {
        'extract_audio': True,
        'audio_format': 'mp3',
        'audio_quality': 0,
        'embed_metadata': True,
        'embed_thumbnail': True,
    },
    'archival': {
        'format': 'bestvideo+bestaudio/best',
        'write_info_json': True,
        'write_thumbnail': True,
        'embed_metadata': True,
    },
    'mobile': {
        'format': 'best[height<=720]',
        'remux_video': 'mp4',
    },
}


class ProfileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.parser = create_parser()
        self.profile_file = self.write_file(PROFILE_FILE_NAME, {'profiles': PROFILES})

    def tearDown(self):
        self._tmpdir.cleanup()

    def write_file(self, name, data):
        path = os.path.join(self.tmpdir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def load(self, data):
        return load_profiles(self.write_file('test.json', data), self.parser)

    def load_options(self, options):
        return self.load({'profiles': {'test': options}})['test'].args

    def assertProfileError(self, data, msg_regex):
        with self.assertRaisesRegex(ProfileError, msg_regex):
            self.load(data)


class TestProfileLoading(ProfileTestCase):
    def test_load_valid_profiles(self):
        profiles = load_profiles(self.profile_file, self.parser)
        self.assertEqual(list(profiles), ['podcast', 'archival', 'mobile'])
        self.assertEqual(profiles['podcast'].options, PROFILES['podcast'])
        self.assertEqual(profiles['podcast'].args, [
            '--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0',
            '--embed-metadata', '--embed-thumbnail'])
        self.assertEqual(profiles['archival'].args, [
            '--format', 'bestvideo+bestaudio/best', '--write-info-json', '--write-thumbnail', '--embed-metadata'])
        self.assertEqual(profiles['mobile'].args, ['--format', 'best[height<=720]', '--remux-video', 'mp4'])

    def test_option_translation(self):
        self.assertEqual(self.load_options({'embed-metadata': True}), ['--embed-metadata'])
        self.assertEqual(self.load_options({'embed_metadata': False}), ['--no-embed-metadata'])
        self.assertEqual(self.load_options({'no_playlist': True}), ['--no-playlist'])
        self.assertEqual(self.load_options({'retries': 3, 'sleep_requests': 0.5}), [
            '--retries', '3', '--sleep-requests', '0.5'])
        self.assertEqual(self.load_options({'parse_metadata': ['title:%(artist)s', 'uploader:%(album)s']}), [
            '--parse-metadata', 'title:%(artist)s', '--parse-metadata', 'uploader:%(album)s'])
        self.assertEqual(self.load_options({'replace_in_metadata': ['title', 'a', 'b']}), [
            '--replace-in-metadata', 'title', 'a', 'b'])
        self.assertEqual(self.load_options({'replace_in_metadata': [['title', 'a', 'b'], ['album', 'c', 'd']]}), [
            '--replace-in-metadata', 'title', 'a', 'b', '--replace-in-metadata', 'album', 'c', 'd'])
        self.assertEqual(self.load_options({'preset_alias': 'mp3'}), ['--preset-alias', 'mp3'])
        self.assertEqual(self.load_options({}), [])

    def test_malformed_file(self):
        self.assertProfileError('{"profiles": {', r'unable to parse profile file')
        self.assertProfileError('', r'unable to parse profile file')

    def test_malformed_structure(self):
        self.assertProfileError([], r'must contain a JSON object with a "profiles" object')
        self.assertProfileError({}, r'must contain a JSON object with a "profiles" object')
        self.assertProfileError({'profiles': ['podcast']}, r'must contain a JSON object with a "profiles" object')
        self.assertProfileError({'profiles': {}, 'extra': 1}, r'unknown top-level keys: extra')
        self.assertProfileError({'profiles': {'test': ['-x']}}, r'profile "test" must be a JSON object')
        self.assertProfileError({'profiles': {'bad name': {}}}, r'invalid profile name "bad name"')

    def test_invalid_options(self):
        for options, msg_regex in (
            ({'not_an_option': True}, r'profile "test": unsupported option "not_an_option"'),
            ({'x': True}, r'unsupported option "x"'),  # Short options are not supported
            ({'extract_aud': True}, r'unsupported option "extract_aud"'),  # Abbreviations are not allowed
            ({'profile': 'podcast'}, r'option "profile" cannot be used in a profile'),
            ({'config_locations': 'x'}, r'option "config_locations" cannot be used in a profile'),
            ({'extract_audio': 'yes'}, r'option "extract_audio" is a switch and must be true or false'),
            ({'extract_audio': False}, r'option "extract_audio" cannot be set to false'),
            ({'audio_format': True}, r'option "audio_format" expects a string or number'),
            ({'audio_format': None}, r'option "audio_format" expects a string or number'),
            ({'audio_format': {'a': 'b'}}, r'option "audio_format" expects a string or number'),
            ({'audio_format': []}, r'option "audio_format" must not be an empty list'),
            ({'replace_in_metadata': ['title', 'a']}, r'option "replace_in_metadata" expects a list of 3'),
        ):
            with self.subTest(options=options):
                self.assertProfileError({'profiles': {'test': options}}, msg_regex)

    def test_invalid_profile_fails_whole_file(self):
        with self.assertRaisesRegex(ProfileError, r'profile "broken": unsupported option'):
            self.load({'profiles': {**PROFILES, 'broken': {'nope': 1}}})

    def test_nonexistent_profile(self):
        with self.assertRaisesRegex(ProfileError, r'profile "nope" is not defined in .+Available profiles: podcast, archival, mobile'):
            resolve_profile('nope', self.profile_file, self.parser)

    def test_resolve_profile(self):
        path, profile = resolve_profile('mobile', self.profile_file, self.parser)
        self.assertEqual(path, self.profile_file)
        self.assertEqual(profile.name, 'mobile')
        self.assertEqual(profile.options, PROFILES['mobile'])


class TestProfileFileDiscovery(ProfileTestCase):
    def test_explicit_file(self):
        self.assertEqual(find_profile_file(self.profile_file), self.profile_file)

    def test_explicit_directory(self):
        self.assertEqual(find_profile_file(self.tmpdir), self.profile_file)

    def test_explicit_file_missing(self):
        missing = os.path.join(self.tmpdir, 'missing.json')
        with self.assertRaisesRegex(ProfileError, r'profile file ".+missing\.json" does not exist'):
            find_profile_file(missing)

    def _config_dir_environ(self):
        return {
            'XDG_CONFIG_HOME': os.path.join(self.tmpdir, 'xdg'),
            'APPDATA': os.path.join(self.tmpdir, 'appdata'),
            'HOME': os.path.join(self.tmpdir, 'home'),
            'USERPROFILE': os.path.join(self.tmpdir, 'home'),
        }

    def test_default_locations(self):
        environ = self._config_dir_environ()
        with set_environ(**environ):
            locations = list(get_profile_file_locations())
        self.assertEqual(locations[:3], [
            os.path.join(environ['XDG_CONFIG_HOME'], 'yt-dlp', PROFILE_FILE_NAME),
            os.path.join(environ['APPDATA'], 'yt-dlp', PROFILE_FILE_NAME),
            os.path.join(environ['HOME'], '.yt-dlp', PROFILE_FILE_NAME),
        ])

    def test_default_file_found(self):
        environ = self._config_dir_environ()
        expected = os.path.join(environ['APPDATA'], 'yt-dlp', PROFILE_FILE_NAME)
        os.makedirs(os.path.dirname(expected))
        with open(expected, 'w', encoding='utf-8') as f:
            json.dump({'profiles': PROFILES}, f)
        with set_environ(**environ):
            path, profiles = load_profile_file(None, self.parser)
        self.assertEqual(path, expected)
        self.assertEqual(list(profiles), list(PROFILES))

    def test_default_file_missing(self):
        with set_environ(**self._config_dir_environ()):
            with self.assertRaisesRegex(ProfileError, r'no profile file was found. Use --profile-file'):
                find_profile_file()


class TestProfileFormatting(ProfileTestCase):
    def test_format_profile_list(self):
        out = format_profile_list(*load_profile_file(self.profile_file, self.parser))
        lines = out.splitlines()
        self.assertEqual(lines[0], f'Available profiles in "{self.profile_file}":')
        self.assertRegex(lines[1], r'^Profile\s+Options$')
        self.assertRegex(lines[2], r'^podcast\s+--extract-audio --audio-format mp3 ')
        self.assertRegex(lines[3], r'^archival\s+--format bestvideo\+bestaudio/best ')
        self.assertRegex(lines[4], r"^mobile\s+--format 'best\[height<=720\]' --remux-video mp4$")

    def test_format_empty_profile_list(self):
        path = self.write_file('empty.json', {'profiles': {}})
        self.assertEqual(format_profile_list(*load_profile_file(path, self.parser)), f'No profiles are defined in "{path}"\n')

    def test_format_profile(self):
        out = format_profile(*resolve_profile('podcast', self.profile_file, self.parser))
        self.assertEqual(out.splitlines(), [
            f'Profile "podcast" from "{self.profile_file}":',
            '  extract_audio: true',
            '  audio_format: "mp3"',
            '  audio_quality: 0',
            '  embed_metadata: true',
            '  embed_thumbnail: true',
            'Equivalent options: --extract-audio --audio-format mp3 --audio-quality 0 --embed-metadata --embed-thumbnail',
        ])


class TestProfileOptions(ProfileTestCase):
    def parse(self, *args):
        return parse_options(['--profile-file', self.profile_file, *args])

    def test_profile_reaches_ydl_opts(self):
        ydl_opts = self.parse('--profile', 'podcast').ydl_opts
        postprocessors = {pp['key']: pp for pp in ydl_opts['postprocessors']}
        self.assertEqual(postprocessors['FFmpegExtractAudio']['preferredcodec'], 'mp3')
        self.assertEqual(postprocessors['FFmpegExtractAudio']['preferredquality'], '0')
        self.assertTrue(postprocessors['FFmpegMetadata']['add_metadata'])
        self.assertIn('EmbedThumbnail', postprocessors)
        self.assertEqual(ydl_opts['format'], 'bestaudio/best')  # Implied by --extract-audio
        self.assertEqual(ydl_opts['final_ext'], 'mp3')

        ydl_opts = self.parse('--profile', 'archival').ydl_opts
        self.assertEqual(ydl_opts['format'], 'bestvideo+bestaudio/best')
        self.assertTrue(ydl_opts['writeinfojson'])
        self.assertTrue(ydl_opts['writethumbnail'])

        ydl_opts = self.parse('--profile', 'mobile').ydl_opts
        self.assertEqual(ydl_opts['format'], 'best[height<=720]')
        self.assertIn({'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mp4'}, ydl_opts['postprocessors'])

    def test_cli_overrides_profile(self):
        # The position of --profile relative to the explicit option must not matter
        for args in (
            ('--profile', 'podcast', '--audio-format', 'flac'),
            ('--audio-format', 'flac', '--profile', 'podcast'),
        ):
            with self.subTest(args=args):
                opts = self.parse(*args).options
                self.assertEqual(opts.audioformat, 'flac')
                self.assertTrue(opts.extractaudio)

        opts = self.parse('--profile', 'podcast', '--no-embed-metadata').options
        self.assertFalse(opts.addmetadata)
        self.assertTrue(opts.embedthumbnail)

    def test_preset_alias_with_profile(self):
        opts = self.parse('--profile', 'mobile', '-t', 'mp3').options
        self.assertEqual(opts.format, 'ba[acodec^=mp3]/ba/b')
        self.assertEqual(opts.remuxvideo, 'mp4')

    def test_profile_precedence_over_config(self):
        config = os.path.join(self.tmpdir, 'yt-dlp.conf')
        with open(config, 'w', encoding='utf-8') as f:
            f.write('--audio-format wav\n--write-description\n')

        _, opts, _ = parseOpts(['--config-locations', config, '--profile-file', self.profile_file, '--profile', 'podcast'])
        self.assertEqual(opts.audioformat, 'mp3')  # profile overrides config
        self.assertTrue(opts.writedescription)  # config is still applied

        _, opts, _ = parseOpts([
            '--config-locations', config, '--profile-file', self.profile_file, '--profile', 'podcast',
            '--audio-format', 'flac'])
        self.assertEqual(opts.audioformat, 'flac')  # command-line overrides profile

    def test_profile_selected_in_config(self):
        config = os.path.join(self.tmpdir, 'yt-dlp.conf')
        with open(config, 'w', encoding='utf-8') as f:
            f.write(f"--profile-file '{self.profile_file}'\n--profile mobile\n")

        _, opts, _ = parseOpts(['--config-locations', config])
        self.assertEqual(opts.remuxvideo, 'mp4')

        _, opts, _ = parseOpts(['--config-locations', config, '--no-profile'])
        self.assertIsNone(opts.remuxvideo)

    def test_no_profile(self):
        missing = os.path.join(self.tmpdir, 'missing.json')
        # The profile file is not accessed at all unless a profile is requested
        _, opts, _ = parseOpts(['--profile-file', missing])
        _, default_opts, _ = parseOpts([])
        self.assertEqual(vars(opts), {**vars(default_opts), 'profile_file': missing})
        self.assertIsNone(default_opts.profile)

        parsed = parse_options([])
        self.assertFalse(parsed.options.extractaudio)
        self.assertEqual([pp['key'] for pp in parsed.ydl_opts['postprocessors']], ['FFmpegConcat'])
        self.assertIsNone(parsed.ydl_opts['format'])

    def test_profile_errors(self):
        with self.assertRaisesRegex(optparse.OptParseError, r'error: profile "nope" is not defined'):
            self.parse('--profile', 'nope')

        missing = os.path.join(self.tmpdir, 'missing.json')
        with self.assertRaisesRegex(optparse.OptParseError, r'error: profile file ".+" does not exist'):
            parseOpts(['--profile-file', missing, '--profile', 'podcast'])

        bad = self.write_file('bad.json', {'profiles': {'test': {'audio_format': True}}})
        with self.assertRaisesRegex(optparse.OptParseError, r'error: profile "test": option "audio_format" expects'):
            parseOpts(['--profile-file', bad, '--profile', 'test'])


class TestProfileCommands(ProfileTestCase):
    def run_main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertIsNone(_real_main(['--profile-file', self.profile_file, *args]))
        return stdout.getvalue()

    def test_list_profiles(self):
        out = self.run_main('--list-profiles')
        self.assertIn(f'Available profiles in "{self.profile_file}":', out)
        for name in PROFILES:
            self.assertRegex(out, rf'(?m)^{name}\s+--')

    def test_show_profile(self):
        out = self.run_main('--show-profile', 'archival')
        self.assertIn('Profile "archival" from', out)
        self.assertIn('  format: "bestvideo+bestaudio/best"', out)
        self.assertIn('Equivalent options: --format bestvideo+bestaudio/best --write-info-json', out)

    def test_show_nonexistent_profile(self):
        with self.assertRaisesRegex(optparse.OptParseError, r'error: profile "nope" is not defined'):
            self.run_main('--show-profile', 'nope')


if __name__ == '__main__':
    unittest.main()
