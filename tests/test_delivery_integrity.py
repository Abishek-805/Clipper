import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from daily_workflow import export_delivery


@pytest.fixture
def job(tmp_path):
    folder = tmp_path / 'job'
    folder.mkdir()
    clip = dict(filename='clip.mp4', start=0, end=2, render=dict(audio_codec='aac', subtitle_cues=1))
    (folder / 'analysis.json').write_text(json.dumps(dict(source='recording.mkv', clips=[clip])))
    (folder / 'clip.mp4').write_bytes(b'original')
    (folder / 'clip.srt').write_text('subtitles')
    return folder


def test_failed_validation_preserves_previous_sidecars(job, tmp_path):
    destination = tmp_path / 'Exports' / 'recording' / 'job'
    destination.mkdir(parents=True)
    old = destination / 'clip.srt'
    old.write_text('owner copy')

    def fail(video, duration, audio, srt, captions_required):
        assert video.read_bytes() == b'original'
        assert video.parent != job
        assert srt.read_text() == 'subtitles'
        assert captions_required
        assert not list(destination.glob('*.mp4'))
        raise ValueError('invalid media')

    with patch('clipper.validate_output', side_effect=fail), pytest.raises(ValueError, match='invalid media'):
        export_delivery(job, 'clip.mp4', tmp_path)
    assert list(destination.iterdir()) == [old]
    assert old.read_text() == 'owner copy'


def test_simultaneous_exports_keep_distinct_complete_bundles(job, tmp_path):
    with patch('clipper.validate_output'), ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: export_delivery(job, 'clip.mp4', tmp_path), range(8)))
    assert len({r['filename'] for r in results}) == 8
    for result in results:
        video = Path(result['folder']) / result['filename']
        assert video.read_bytes() == b'original'
        assert video.with_suffix('.srt').read_text() == 'subtitles'
        assert json.loads(video.with_suffix('.json').read_text())['job_id'] == 'job'
