import unittest
from unittest.mock import Mock, patch
from bridge.camera_sources import Esp32LiveSource

class LiveCameraTests(unittest.TestCase):
    @patch('bridge.camera_sources.http.client.HTTPConnection')
    def test_fragmented_frames_share_one_stream_and_close_releases_it(self, factory):
        connection = factory.return_value
        response = connection.getresponse.return_value
        response.status = 200
        response.headers = {'Content-Type':'multipart/x-mixed-replace;boundary=frame'}
        first = b'\xff\xd8\xff\xe0first\xff\xd9'
        second = b'\xff\xd8\xff\xe0second\xff\xd9'
        response.read1.side_effect = [b'--frame\r\n'+first[:2], first[2:]+b'\r\n--frame\r\n'+second]
        source = Esp32LiveSource('http://192.168.0.2/capture')
        self.assertEqual(source.capture(), (first,'image/jpeg'))
        self.assertEqual(source.capture(), (second,'image/jpeg'))
        self.assertEqual(factory.call_count, 1)
        connection.request.assert_called_once_with('GET','/stream')
        source.close()
        connection.close.assert_called_once()

    @patch('bridge.camera_sources.Esp32SnapshotSource.capture', return_value=(b'jpeg','image/jpeg'))
    @patch('bridge.camera_sources.http.client.HTTPConnection')
    def test_snapshot_only_server_falls_back_without_repeated_stream_probes(self, factory, snapshot):
        response = factory.return_value.getresponse.return_value
        response.status = 404
        response.headers = {}
        source = Esp32LiveSource('http://192.168.0.2/capture')
        self.assertEqual(source.capture(),(b'jpeg','image/jpeg'))
        self.assertEqual(source.capture(),(b'jpeg','image/jpeg'))
        self.assertEqual(factory.call_count,1)
        self.assertEqual(snapshot.call_count,2)
