import math
import os
import time
import unittest

from tests import linux
from atticus_shell import pen as P
from atticus_shell.desktop.bridge import Output, PointerUnavailable


class Clock:
    def __init__(self):
        self.t = 10.0

    def __call__(self):
        return self.t


def circle(n=120, r=80, cx=400, cy=300):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


class SamplerTests(unittest.TestCase):
    def test_records_time_x_y(self):
        clock, spots = Clock(), iter([(1, 2), (3, 4)])
        s = P.PointerSampler(lambda: next(spots), now=clock)
        s.start()
        s.tick()
        clock.t += 0.016
        s.tick()
        self.assertEqual(s.stop(), [[0.0, 1.0, 2.0], [0.016, 3.0, 4.0]])

    def test_caps_at_max_samples(self):
        clock = Clock()
        s = P.PointerSampler(lambda: (1, 1), now=clock)
        s.start()
        for _ in range(P.MAX_SAMPLES + 50):
            clock.t += 0.01
            s.tick()
        self.assertEqual(len(s.stop()), P.MAX_SAMPLES)

    def test_sway_uses_capture_surface(self):
        def unavailable():
            raise PointerUnavailable('no pointer')
        s = P.PointerSampler(unavailable, now=Clock())
        s.start()
        s.tick()
        self.assertTrue(s.needs_capture)
        s.motion(5, 6)          # the capture surface's motion events
        self.assertEqual(s.stop()[0][1:], [5.0, 6.0])


class SettleTests(unittest.TestCase):
    def test_still_pointer_without_this_is_dropped(self):
        samples = [[0, 100, 100], [0.5, 105, 102]]
        self.assertEqual(P.settle(samples, 'open spotify', classify=lambda s, t: self.fail('no classify')), ([], 'open spotify'))

    def test_this_keeps_a_still_pointer_for_the_classifier(self):
        samples = [[0, 100, 100], [0.5, 105, 102]]
        kept, text = P.settle(samples, 'what is this', classify=lambda s, t: {'meaningful': True})
        self.assertEqual(kept, samples)

    def test_classifier_says_not_meaningful(self):
        samples = [[i * 0.02, x, y] for i, (x, y) in enumerate(circle())]
        self.assertEqual(P.settle(samples, 'hm', classify=lambda s, t: {'meaningful': False})[0], [])

    def test_silent_ask_fills_empty_words(self):
        samples = [[i * 0.02, x, y] for i, (x, y) in enumerate(circle())]
        self.assertEqual(P.settle(samples, '', classify=lambda s, t: {'meaningful': True, 'silent': 'What is this?'})[1], 'What is this?')

    def test_samples_feed_read_gesture(self):
        import atticus
        samples = [[i * 0.02, x, y] for i, (x, y) in enumerate(circle())]
        self.assertEqual(atticus.read_gesture(samples).kind, 'circle')


class PointContextTests(unittest.TestCase):
    outs = [Output('eDP-1', 0, 0, 1280, 800), Output('HDMI-1', 1280, 0, 1920, 1080)]

    def test_region_padded_and_clipped(self):
        self.assertEqual(P.point_region([(10, 10), (60, 40)], self.outs), (0, 0, 84, 64))
        self.assertEqual(P.point_region([(1300, 500), (1400, 600)], self.outs), (1280, 476, 144, 148))

    def test_context_with_image_and_element(self):
        ctx = P.point_context([(100, 100), (200, 150)], self.outs, capture=lambda region, path: True, element=lambda pt: 'Firefox AXButton: Save')
        self.assertEqual(ctx['bounds'], [76, 76, 148, 98])
        self.assertTrue(ctx['image'].endswith('.jpg'))
        self.assertEqual(ctx['element'], 'Firefox AXButton: Save')
        self.assertEqual(ctx['path'], [[100, 100], [200, 150]])

    def test_nothing_captured_and_no_element_is_none(self):
        self.assertIsNone(P.point_context([(5, 5)], self.outs, capture=lambda r, p: False, element=lambda pt: ''))

    def test_off_screen_point_is_none(self):
        self.assertIsNone(P.point_context([(-500, -500)], self.outs, capture=lambda r, p: True, element=lambda pt: 'x'))


@unittest.skipUnless(linux.HAVE_GI and os.environ.get('DISPLAY'), 'needs a display')
class OverlayTests(unittest.TestCase):
    def test_trail_is_click_through(self):
        from atticus_shell import windows
        from atticus_shell.desktop.env import DesktopEnv
        from tests.linux.test_web_bridge import spin
        trail = P.TrailOverlay(Output('screen', 0, 0, 800, 600), DesktopEnv('x11', 'x11'))
        trail.add(10, 10)
        trail.add(50, 60)
        trail.show()
        self.assertTrue(spin(lambda: windows.input_rects(trail.window) == [], 3))

    def test_point_mode_clicks_reach_box(self):
        from atticus_shell import windows
        from atticus_shell.desktop.env import DesktopEnv
        from tests.linux.test_web_bridge import spin
        view = P.PointerView(Output('screen', 0, 0, 800, 600), DesktopEnv('x11', 'x11'), box=(14, 10, 500, 200), done=lambda pts: None)
        view.show()
        rects = lambda: sorted(windows.input_rects(view.window))
        self.assertTrue(spin(lambda: rects() and all(not (14 <= x < 514 and 10 <= y < 210 and w <= 500) for x, y, w, h in rects()) and
                             sum(w * h for x, y, w, h in rects()) == 800 * 600 - 500 * 200, 3), rects())

    def test_stroke_reports_points(self):
        from atticus_shell.desktop.env import DesktopEnv
        got = []
        view = P.PointerView(Output('screen', 0, 0, 800, 600), DesktopEnv('x11', 'x11'), box=(0, 0, 1, 1), done=got.append)
        view.press(100, 100)
        view.move(150, 120)
        view.release(160, 130)
        self.assertEqual(got, [[(100, 100), (150, 120), (160, 130)]])


if __name__ == '__main__':
    unittest.main()
