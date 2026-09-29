"""Browser check of the review workspace (needs the demo server on :8010 and Google Chrome).

    .venv/Scripts/python tests/ui_demo_flow.py [screenshot_dir]

At 1920x1080: PR selector, dataset review of another PR, a PR without a saved review,
custom-code fast memory check, then PR #137 reject -> feedback retained -> review again.
Asserts on real rendered content and saves a screenshot of each step.
"""

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:8010"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
SLOW = 60000


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")

    # 1. initial: PR #137 with its saved review
    expect(page.locator("#pr-title")).to_contain_text("Add refund processing fee")
    expect(page.locator(".finding")).to_have_count(4)
    expect(page.locator("#status")).to_contain_text("Hindsight memory connected", timeout=15000)
    expect(page.locator("#code")).to_contain_text("httpx.Client(")
    options = page.locator("#pr-select option")
    assert options.count() >= 8, options.count()
    shot(page, "1-pr137")

    # 2. Run review on #137: real pipeline (analyze + Hindsight recall + saved findings)
    page.locator("#run-btn").click()
    expect(page.locator("#pipeline li.done")).to_have_count(5, timeout=SLOW)
    expect(page.locator("#pipeline")).to_contain_text("retrieved")
    shot(page, "2-pr137-pipeline")

    # 3. select another PR with a saved review (#139) and run it
    page.locator("#pr-select").select_option("139")
    expect(page.locator("#pr-title")).to_contain_text("PR #139")
    expect(page.locator("#file-tabs")).to_contain_text("network_health.py")
    page.locator("#run-btn").click()
    expect(page.locator("#pipeline li.done")).to_have_count(5, timeout=SLOW)
    expect(page.locator(".finding").first).to_be_visible()
    shot(page, "3-pr139-review")

    # 4. a PR without a saved full review: honest fast memory check
    page.locator("#pr-select").select_option("140")
    expect(page.locator("#pr-title")).to_contain_text("PR #140")
    page.locator("#run-btn").click()
    expect(page.locator("#findings")).to_contain_text("No saved full review for PR #140", timeout=SLOW)
    expect(page.locator(".pattern").first).to_be_visible()
    expect(page.locator(".mem-item").first).to_be_visible()
    shot(page, "4-pr140-memory-check")

    # 5. Try your own code: fast memory check on the example snippet
    page.locator('[data-mode="custom"]').click()
    expect(page.locator("#editor")).to_be_visible()
    page.locator("#run-btn").click()
    expect(page.locator("#pipeline")).to_contain_text("Fast memory check ready", timeout=SLOW)
    expect(page.locator(".pattern")).to_contain_text(["Outbound HTTP call"])
    expect(page.locator(".mem-item").first).to_be_visible()
    shot(page, "5-custom-code")

    # 6. back to PR #137: reject F1 -> feedback retained (existing FeedbackStore path)
    page.locator('[data-mode="dataset"]').click()
    page.locator("#pr-select").select_option("137")
    expect(page.locator(".finding")).to_have_count(4)
    page.locator('[data-reject="F1"]').click()
    expect(page.locator("#reason")).to_have_value("Approved exception for this integration.")
    page.locator("#modal-submit").click()
    card = page.locator('.finding[data-id="F1"] .result-card')
    expect(card.locator("h4")).to_contain_text("Feedback retained in Hindsight", timeout=180000)
    expect(card).to_contain_text("experience")
    expect(card).to_contain_text("PR #137 · Finding F1")
    shot(page, "6-feedback-retained")

    # 7. review again: latest saved run + live recall of the feedback, honestly labelled
    page.locator("#review-again").click()
    expect(page.locator("#run-notice")).to_contain_text("Latest ReviewMind run still raises F1")
    expect(page.locator(".recall-card")).to_contain_text("Developer feedback is now recallable from Hindsight", timeout=SLOW)
    shot(page, "7-review-again")

    browser.close()
    assert not errors, errors
    print("UI workspace flow OK; screenshots in", OUT)
