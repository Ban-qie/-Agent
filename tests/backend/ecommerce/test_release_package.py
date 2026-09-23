from pathlib import PurePosixPath

import pytest

from devtools.v4_release_package import assert_safe_path, selected_tracked_path


def test_release_allowlist_includes_runtime_and_excludes_private_evidence():
    assert selected_tracked_path('py-src/data_formulator/ecommerce/production_app.py')
    assert selected_tracked_path('src/app.tsx')
    assert selected_tracked_path('deploy/ecommerce/compose.yml')
    assert selected_tracked_path('devtools/backup_ecommerce.py')
    assert not selected_tracked_path('docs/verification/V4-S04/handoff.md')
    assert not selected_tracked_path('.local/verification/qwen-usage.json')
    assert not selected_tracked_path('data/raw/olist/orders.csv')


@pytest.mark.parametrize('path', [
    '../secret', '/absolute/secret', '.local/private.json',
    'deploy/ecommerce/.env.v4.private', 'docs/verification/private.json',
])
def test_release_paths_reject_private_or_escaping_content(path):
    with pytest.raises(ValueError):
        assert_safe_path(PurePosixPath(path).as_posix())
