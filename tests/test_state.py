from pathlib import Path
from src.state import StateRepository


def test_state_repository_flow(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    repo = StateRepository(str(db_file))

    # Test meta functions
    repo.set_meta("test_key", "test_val")
    assert repo.get_meta("test_key") == "test_val"
    assert repo.get_meta("non_existent") is None

    # Test job lifecycle
    job_id = repo.create_job(url="https://youtube.com/watch?v=123")
    assert isinstance(job_id, str)
    assert len(job_id) == 12

    job = repo.get_job(job_id)
    assert job is not None
    assert job["url"] == "https://youtube.com/watch?v=123"
    assert job["status"] == "queued"
    assert job["progress"] == 0

    # Test updating job
    repo.update_job(job_id, status="downloading", progress=20, video_id="vid_123")
    job = repo.get_job(job_id)
    assert job["status"] == "downloading"
    assert job["progress"] == 20
    assert job["video_id"] == "vid_123"

    # Test listing jobs
    jobs = repo.list_jobs(limit=10)
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id

    # Test failing a job
    repo.mark_job_failed(job_id, "Some fatal error")
    job = repo.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "Some fatal error"
    assert job["current_step"] == "失败"

    repo.close()
