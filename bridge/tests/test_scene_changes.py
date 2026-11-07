import unittest

from bridge.scene_changes import SceneCadence, changed, scene_signature


class SceneCadenceTests(unittest.TestCase):
    def test_changed_mock_item_is_due_while_unchanged_scene_waits(self):
        cadence = SceneCadence()
        bottle = scene_signature((b'bottle', 'image/svg+xml'))
        can = scene_signature((b'can', 'image/svg+xml'))
        self.assertTrue(cadence.due(bottle, 0, ready_to_sort=True))
        cadence.sent(bottle, 0)
        self.assertFalse(cadence.due(bottle, 1, ready_to_sort=True))
        self.assertTrue(cadence.due(can, 1, ready_to_sort=True))
        cadence.sent(can, 1)
        self.assertFalse(cadence.due(can, 2, ready_to_sort=True))
        self.assertTrue(cadence.due(can, 13, ready_to_sort=True))

    def test_new_item_waiting_for_arm_retries_when_ready(self):
        cadence = SceneCadence()
        can = scene_signature((b'can', 'image/svg+xml'))
        cadence.sent(can, 4, waiting_for_arm=True)
        self.assertFalse(cadence.due(can, 5, ready_to_sort=False))
        self.assertTrue(cadence.due(can, 5, ready_to_sort=True))

    def test_empty_scene_gets_second_verification_without_cloud_spam(self):
        cadence = SceneCadence()
        empty = scene_signature((b'empty', 'image/svg+xml'))
        cadence.sent(empty, 0, verify_empty=True)
        self.assertFalse(cadence.due(empty, 2, ready_to_sort=True))
        self.assertTrue(cadence.due(empty, 3, ready_to_sort=True))

    def test_small_camera_noise_does_not_count_as_new_item(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('Optional camera dependencies unavailable')
        first = np.full((32, 32), 100, dtype=np.uint8)
        noise = np.full((32, 32), 105, dtype=np.uint8)
        swapped = np.full((32, 32), 125, dtype=np.uint8)
        self.assertFalse(changed(noise, first))
        self.assertTrue(changed(swapped, first))


if __name__ == '__main__':
    unittest.main()
