"""Assignment scenarios executed through the LangGraph workflow."""

from app.evaluation.suite import run_suite


async def test_assignment_scenarios():
    results = await run_suite()
    failed = [item for item in results if not item["passed"]]
    rendered = "\n".join(f"{item['id']}: {item['detail']} | {item['last_say']}" for item in failed)
    assert not failed, rendered
