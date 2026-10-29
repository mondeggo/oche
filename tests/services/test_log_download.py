from pathlib import Path
import tempfile
import unittest

from app.log_download import log_chunks


class LogDownloadTests(unittest.TestCase):
    def test_download_stops_at_initial_size_while_file_grows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'service.log'
            path.write_bytes(b'a' * 70000)
            chunks = log_chunks(path)
            first = next(chunks)
            with path.open('ab') as log:
                log.write(b'new data')
            self.assertEqual(first + b''.join(chunks), b'a' * 70000)

    def test_clear_during_download_finishes_without_length_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'service.log'
            path.write_bytes(b'a' * 70000)
            chunks = log_chunks(path)
            self.assertEqual(len(next(chunks)), 65536)
            with path.open('r+b') as log:
                log.truncate(0)
            self.assertEqual(b''.join(chunks), b'')
