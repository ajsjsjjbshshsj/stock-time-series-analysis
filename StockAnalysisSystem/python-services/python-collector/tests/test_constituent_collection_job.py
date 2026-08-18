from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest


def test_constituent_job_collects_and_stores_one_snapshot():
    from app.jobs.constituent_collection_job import ConstituentCollectionJob

    collector = MagicMock(source_name='akshare')
    collector.collect_index_constituents.return_value = pd.DataFrame({
        'group_type': ['index'],
        'group_code': ['000300.SH'],
        'ts_code': ['000001.SZ'],
        'as_of_date': [date(2026, 8, 14)],
        'weight': [0.5],
        'source': ['akshare'],
    })
    market_repo = MagicMock()
    market_repo.replace_constituents.return_value = 1
    task_repo = MagicMock()
    task_repo.is_task_success.return_value = False
    task_repo.create_task.return_value = 31

    result = ConstituentCollectionJob(
        collector, market_repo, task_repo
    ).execute('index', '000300.SH', '2026-08-14')

    assert result['success'] is True
    assert result['record_count'] == 1
    collector.collect_index_constituents.assert_called_once_with(
        '000300.SH', '20260814'
    )
    task_repo.is_task_success.assert_called_once_with(
        'constituent', 'index:000300.SH:20260814', 'akshare'
    )
    market_repo.replace_constituents.assert_called_once()
    task_repo.update_status.assert_called_with(31, 'SUCCESS', record_count=1)


def test_constituent_job_rejects_unknown_group_type():
    from app.jobs.constituent_collection_job import ConstituentCollectionJob

    with pytest.raises(ValueError, match='group_type'):
        ConstituentCollectionJob(
            MagicMock(), MagicMock(), MagicMock()
        ).execute('theme', 'AI', '20260814')
