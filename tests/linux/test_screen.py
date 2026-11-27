import io
import json
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests import linux
from atticus_shell import screen as S
from atticus_shell.desktop import ids
from atticus_shell.desktop.bridge import Output, Window, png_size, write_png
from tests.linux.fakes import FakeBridge

HAVE_CAIRO = linux.HAVE_GI


def desk():
    return FakeBridge([Output('eDP-1', 0, 0, 1280, 800, 1.0, True), Output('HDMI-1', 1280, 0, 1920, 1080, 1.5, False)],
                      [Window('0x1a', 'firefox', 10, 'Docs — Firefox', 1472, 108, 960, 540, 'HDMI-1', '0', focused=True),
                       Window('atticus-box', 'atticus', 99, 'Atticus box', 0, 0, 500, 200, 'eDP-1', '0')])


class GeometryTests(unittest.TestCase):
    def test_crop_maps_through_scale_1_5(self):
        out = Output('HDMI-1', 1280, 0, 1920, 1080, 1.5)
        self.assertEqual(S.picture_rect((1472, 108, 960, 540), out, 1280, 720), (128, 72, 640, 360))

    def test_picture_height_keeps_aspect(self):
        self.assertEqual(S.picture_size(Output('x', 0, 0, 1920, 1080)), (1280, 720))
        self.assertEqual(S.picture_size(Output('x', 0, 0, 1280, 800)), (1280, 800))

    def test_portal_crop_of_whole_desktop(self):
        outs = [Output('a', 0, 0, 1280, 800), Output('b', 1280, 0, 1920, 1080)]
        # the portal pictured the whole 3200x1080 layout at 2x: output b is the right part
        self.assertEqual(S.desktop_crop(outs[1], outs, 6400, 2160), (2560, 0, 3840, 2160))


class IdTests(unittest.TestCase):
    def test_hex_and_numeric_ids(self):
        self.assertEqual(ids.public_id('0x1a'), '26')
        self.assertEqual(ids.public_id('11'), '11')

    def test_uuid_ids_round_trip(self):
        pub = ids.public_id('{5e1f-aa}')
        self.assertTrue(pub.isdigit())
        self.assertEqual(ids.public_id('{5e1f-aa}'), pub)
        self.assertEqual(ids.resolve(pub, ['{5e1f-aa}', '{other}']), '{5e1f-aa}')

    def test_resolve_hex(self):
        self.assertEqual(ids.resolve('26', ['0x1a', '0x2']), '0x1a')
        self.assertIsNone(ids.resolve('999', ['0x1a']))


class SharingTests(unittest.TestCase):
    def test_sharing_detected_from_pw_dump_fixture(self):
        dump = [{'type': 'PipeWire:Interface:Node', 'info': {'props': {'media.class': 'Video/Source', 'application.name': 'xdg-desktop-portal-gnome'}}},
                {'type': 'PipeWire:Interface:Node', 'info': {'props': {'media.class': 'Audio/Sink', 'node.name': 'speakers'}}}]
        self.assertTrue(S.sharing_active(json.dumps(dump)))
        self.assertFalse(S.sharing_active(json.dumps(dump[1:])))
        self.assertFalse(S.sharing_active('not json'))

    def test_webcam_is_not_sharing(self):
        dump = [{'type': 'PipeWire:Interface:Node', 'info': {'props': {'media.class': 'Video/Source', 'node.name': 'v4l2_input.usb-cam'}}}]
        self.assertFalse(S.sharing_active(json.dumps(dump)))


class DrawTargetTests(unittest.TestCase):
    def test_draw_target_json_shape_matches_swift(self):
        t = S.draw_target(desk(), screen='HDMI-1')
        self.assertEqual(set(t), {'pid', 'windowID', 'windowFrame', 'window', 'app', 'minimized'})
        self.assertEqual((t['windowID'], t['windowFrame'], t['app']), ('26', [1472, 108, 960, 540], 'firefox'))

    def test_atticus_windows_never_targets(self):
        b = FakeBridge(desk().outputs(), [Window('atticus-box', 'atticus', 99, 'Atticus box', 0, 0, 500, 200, 'eDP-1', '0', focused=True)])
        self.assertIsNone(S.draw_target(b, screen=''))

    def test_screens_rows_like_brain_expects(self):
        rows = S.screens_rows(desk(), visible=False)
        self.assertEqual(rows[1], ['HDMI-1', 1280, 0, 1920, 1080, False])
        self.assertTrue(rows[0][5])


class ImageSizeTests(unittest.TestCase):
    def test_png_and_jpeg(self):
        import brain_linux
        root = tempfile.mkdtemp()
        png = os.path.join(root, 'a.png')
        write_png(png, 7, 3, b'\0' * 84)
        self.assertEqual(brain_linux.image_size(png), (7, 3))
        jpg = os.path.join(root, 'a.jpg')   # SOI, APP0, SOF0 with 720x1280
        Path(jpg).write_bytes(b'\xff\xd8' + b'\xff\xe0\x00\x10' + b'JFIF\0' + b'\0' * 9 + b'\xff\xc0\x00\x11\x08' + struct.pack('>HH', 720, 1280) + b'\x03' + b'\0' * 9)
        self.assertEqual(brain_linux.image_size(jpg), (1280, 720))
        self.assertIsNone(brain_linux.image_size(os.path.join(root, 'missing')))


@unittest.skipUnless(HAVE_CAIRO, 'needs pycairo and GdkPixbuf (test image)')
class LookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.events = []

    def look(self, args, bridge=None):
        out = io.StringIO()
        with redirect_stdout(out):
            code = S.look(args, bridge or desk(), hide=lambda: self.events.append('hide'))
        return code, out.getvalue()

    def jpeg_size(self, path):
        import brain_linux
        return brain_linux.image_size(path)

    def test_look_hides_overlays_then_captures_1280_jpeg(self):
        path = os.path.join(self.tmp, 's.jpg')
        b = desk()
        orig = b.screenshot_output
        b.screenshot_output = lambda n, p: (self.events.append('shot'), orig(n, p))[1]
        code, out = self.look(['2', path], b)
        self.assertEqual(code, 0)
        self.assertEqual(self.events, ['hide', 'shot'])
        self.assertEqual(self.jpeg_size(path), (1280, 720))
        self.assertEqual(Path(path).read_bytes()[:2], b'\xff\xd8')

    def test_window_painted_where_it_sits(self):
        path = os.path.join(self.tmp, 's.jpg')
        code, out = self.look(['2', path, '26'])
        self.assertEqual(code, 0)
        self.assertIn('window 1472 108 960 540', out)

    def test_marks_print_pointed(self):
        path = os.path.join(self.tmp, 's.jpg')
        mark = {'kind': 'circle', 'path': [[100, 100], [200, 100], [150, 180]], 'spots': [[1, 150, 130, 0]][:0], 'box': [], 'scale': 1}
        code, out = self.look(['1', path, '', json.dumps(mark)])
        self.assertEqual(code, 0)
        self.assertIn('pointed', out.split())

    def test_bad_marks_are_ignored(self):
        path = os.path.join(self.tmp, 's.jpg')
        code, out = self.look(['1', path, '', json.dumps({'kind': 'circle', 'path': [[1]], 'spots': [], 'box': [], 'scale': 1})])
        self.assertEqual(code, 0)
        self.assertNotIn('pointed', out)

    def test_step_screen_samples_every_output(self):
        pixels = S.step_screen(desk(), hide=lambda: None)
        self.assertEqual(len(pixels), 2 * 64 * 40)
        self.assertTrue(all(0 <= p <= 255 for p in pixels))

    def test_failed_capture_exits_1(self):
        b = desk()
        b.screenshot_output = lambda n, p: False
        code, _ = self.look(['1', os.path.join(self.tmp, 'x.jpg')], b)
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
