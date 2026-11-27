import subprocess
import unittest
from unittest import mock

from tests import linux


class FakeSpotify:
    def __init__(self, running=True, plays=True):
        self.running, self.plays, self.track, self.status, self.calls = running, plays, '/com/spotify/track/old', 'Paused', []

    def __call__(self, args, **kw):
        self.calls.append(args)
        done = lambda out='', code=0: subprocess.CompletedProcess(args, code, out, '')
        if args[0] == 'gtk-launch':
            self.running = True
            return done()
        if args[0] != 'gdbus':
            return done()
        if 'NameHasOwner' in ' '.join(args):
            return done('(%s,)' % ('true' if self.running else 'false'))
        if not self.running:
            return done('', 1)
        if 'org.mpris.MediaPlayer2.Player.OpenUri' in args:
            if self.plays:
                self.track = '/com/spotify/track/' + args[-1].split(':')[-1]
                self.status = 'Playing'
            return done('()')
        if args[-1] == 'PlaybackStatus':
            return done(f"(<'{self.status}'>,)")
        if args[-1] == 'Metadata':
            return done("(<{'mpris:trackid': <'%s'>, 'xesam:title': <'Blinding Lights'>, 'xesam:artist': <['The Weeknd']>}>,)" % self.track)
        return done()


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        self.a = linux.linux_brain(self)
        for p in (mock.patch.object(self.a, 'spotify_item', return_value=('spotify:track:abc123', 'Blinding Lights - The Weeknd')),
                  mock.patch.object(self.a, 'preview_app'), mock.patch.object(self.a, 'keep_behind'), mock.patch('time.sleep')):
            p.start()
            self.addCleanup(p.stop)

    def test_play_verifies_title_via_mpris(self):
        fake = FakeSpotify()
        with mock.patch('subprocess.run', side_effect=fake):
            self.assertEqual(self.a.play('blinding lights'), 'Playing Blinding Lights - The Weeknd on Spotify.')
        self.assertTrue(any('OpenUri' in ' '.join(c) for c in fake.calls))

    def test_launches_spotify_when_closed(self):
        fake = FakeSpotify(running=False)
        import brain_linux
        with mock.patch('subprocess.run', side_effect=fake), mock.patch.object(brain_linux, '_find', return_value=mock.Mock(id='com.spotify.Client')):
            self.assertTrue(self.a.play('blinding lights').startswith('Playing'))
        self.assertIn(['gtk-launch', 'com.spotify.Client'], fake.calls)

    def test_never_reached_playing(self):
        fake = FakeSpotify(plays=False)
        with mock.patch('subprocess.run', side_effect=fake):
            self.assertEqual(self.a.play('blinding lights'), 'Could not start Blinding Lights - The Weeknd in Spotify: playback did not reach the playing state.')

    def test_nothing_found(self):
        with mock.patch.object(self.a, 'spotify_item', return_value=None):
            self.assertIsNone(self.a.play('zzzz'))

    def test_queue_neither_says_how(self):
        self.assertIn('Spotify', self.a.queue('a song'))

    def test_playlist_cache_only(self):
        self.assertIsNone(self.a.playlist('road trip'))


if __name__ == '__main__':
    unittest.main()
