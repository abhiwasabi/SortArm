"""Exercise the real ArmController contract, not just a fake bridge fixture."""
import unittest

from bridge.core import Bridge

try:
    from arm import BINS, ArmConfig, ArmController, MockBackend, demo_poses
except ImportError:
    ArmController = None


@unittest.skipIf(ArmController is None, 'Paul\'s arm/ dependency (#20) is not on the import path')
class ControllerIntegrationTests(unittest.TestCase):
    def test_home_sort_done_count_and_stop_recovery(self):
        config = ArmConfig(speed_deg_s=90, gripper_speed=300, rate_hz=1000,
                           grip_dwell_s=0, release_dwell_s=0)
        arm = ArmController(MockBackend(), demo_poses(), config)
        bridge = Bridge(arm, simulation=True, allowed_bins=BINS)
        try:
            bridge.camera_frame((b'frame', 'image/jpeg'))
            self.assertTrue(bridge.home())
            self.assertTrue(arm.wait_idle(15))
            self.assertEqual(bridge.counts, {'PAPER': 0, 'PLASTIC': 0, 'METAL': 0, 'ORGANIC': 0})
            if set(BINS) != {'PAPER', 'PLASTIC', 'METAL', 'ORGANIC'}:
                with self.assertRaisesRegex(ValueError, 'must support'):
                    bridge.set_sorting(True)
                return
            bridge.set_sorting(True)
            ticket = bridge.begin_prediction()
            self.assertTrue(bridge.accept_prediction(ticket, dict(label='Plastic bottle', bin='PLASTIC',
                            confidence=None, item_present=True, needs_review=False)))
            self.assertTrue(arm.wait_idle(15))
            self.assertEqual(bridge.snapshot()['counts']['PLASTIC'], 1)
            bridge.stop()
            self.assertFalse(bridge.enabled)
            self.assertEqual(arm.state, 'ERROR')
        finally:
            arm.close()
