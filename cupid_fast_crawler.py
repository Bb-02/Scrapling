"""
51job FAST crawler — 0.3s delay, no cycle rest, WAF → save + exit.
Shares progress + output with cupid_search_crawler.py.
User manually switches IP between runs.

Usage:
    python cupid_fast_crawler.py
    python cupid_fast_crawler.py --delay 0.5
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

import cupid_search_crawler as slow

# ============================================================
# Constants (override)
# ============================================================

FAST_DELAY = 0.3

# ============================================================
# Fast crawl loop (overrides slow.crawl)
# ============================================================

def crawl():
    logger = logging.getLogger("fast")
    slow.OUTPUT_DIR.mkdir(exist_ok=True)

    progress = slow.load_progress()
    kw_idx = progress.get("kw_idx", 0)
    phase = progress.get("phase", "scout")
    area_idx = progress.get("area_idx", 0)
    all_rows: list[dict] = progress.get("all_rows", [])
    seen_ids: set = set(progress.get("seen_ids", []))

    # --- Resume from Excel (same logic as slow, but fast-friendly) ---
    resume_path = slow.OUTPUT_DIR / "cupid_output.xlsx"
    if resume_path.exists():
        try:
            df = pd.read_excel(resume_path)
            if len(df) < 1000:
                resume_path = None
        except Exception:
            resume_path = None
    if resume_path is None:
        backups = sorted(slow.OUTPUT_DIR.glob("cupid_output_20*.xlsx"),
                         key=lambda x: x.stat().st_size, reverse=True)
        for bk in backups[:2]:
            try:
                df = pd.read_excel(bk)
                if len(df) > 1000:
                    resume_path = bk
                    break
            except Exception:
                continue
    if resume_path and not all_rows:
        df = pd.read_excel(resume_path)
        for _, row in df.iterrows():
            d = row.to_dict()
            jid = slow.extract_job_id_from_url(str(d.get("岗位链接", "")))
            if jid and jid not in seen_ids:
                seen_ids.add(jid)
                all_rows.append(d)
        for i, row in enumerate(all_rows):
            row["序号"] = i + 1
        logger.info("Resumed %d rows from %s", len(all_rows), resume_path.name)
    elif not all_rows:
        logger.warning("No valid Excel found — starting fresh")

    # Normalize seen_ids
    new_seen = set()
    for sid in seen_ids:
        if "51job.com" in sid or ".html" in sid:
            jid = slow.extract_job_id_from_url(sid)
            if jid:
                new_seen.add(jid)
        else:
            new_seen.add(sid)
    seen_ids = new_seen

    # --- Keywords & areas ---
    keywords = slow.all_keywords()
    areas = list(slow.PROVINCE_CODES.items())
    scout_areas = slow.SCOUT_AREAS
    scout_codes = {c for _, c in scout_areas}
    full_areas = [(n, c) for n, c in areas if c not in scout_codes]

    total_kw = len(keywords)
    logger.info("FAST mode: %d keywords, delay=%.1fs, pageSize=%d",
                total_kw, slow.DELAY_SEC, slow.PAGE_SIZE)
    logger.info("WAF strategy: save + exit (you switch IP and re-run)")

    try:
        while kw_idx < total_kw:
            category, keyword = keywords[kw_idx]
            logger.info("[%d/%d] %s | %s | phase=%s",
                       kw_idx + 1, total_kw, category, keyword, phase)

            try:
                if phase == "scout":
                    before = len(all_rows)
                    slow.crawl_areas(category, keyword, scout_areas,
                                     all_rows, seen_ids, logger)
                    progress["total"] = len(all_rows)
                    progress["seen_ids"] = list(seen_ids)
                    if len(all_rows) > before:
                        logger.info("Scout +%d rows, starting full", len(all_rows) - before)
                        phase = "full"
                        area_idx = 0
                    else:
                        logger.info("Scout empty, skip")
                        kw_idx += 1
                        area_idx = 0
                        phase = "scout"

                elif phase == "full":
                    remaining = full_areas[area_idx:]
                    last_ai = slow.crawl_areas(category, keyword, remaining,
                                               all_rows, seen_ids, logger,
                                               area_offset=area_idx)
                    area_idx += last_ai
                    if area_idx >= len(full_areas):
                        kw_idx += 1
                        area_idx = 0
                        phase = "scout"
                    progress["total"] = len(all_rows)
                    progress["seen_ids"] = list(seen_ids)

                # Save after every keyword
                progress.update(kw_idx=kw_idx, area_idx=area_idx, phase=phase)
                slow.save_progress(progress)
                slow._save_output(all_rows)
                logger.info("Saved: %d rows | next kw_idx=%d", len(all_rows), kw_idx)

            except slow.WafBlocked:
                # Save and EXIT — user switches IP manually
                progress["total"] = len(all_rows)
                progress["seen_ids"] = list(seen_ids)
                progress.update(kw_idx=kw_idx, area_idx=area_idx, phase=phase)
                slow.save_progress(progress)
                slow._save_output(all_rows)
                logger.warning(
                    "WAF blocked! Saved %d rows at kw_idx=%d. "
                    "Switch IP and re-run: python cupid_fast_crawler.py",
                    len(all_rows), kw_idx,
                )
                sys.exit(1)

    except KeyboardInterrupt:
        progress["total"] = len(all_rows)
        progress["seen_ids"] = list(seen_ids)
        progress.update(kw_idx=kw_idx, area_idx=area_idx, phase=phase)
        slow.save_progress(progress)
        slow._save_output(all_rows)
        logger.info("Interrupted. Saved %d rows at kw_idx=%d", len(all_rows), kw_idx)

    slow._save_output(all_rows)
    logger.info("DONE! Total: %d rows", len(all_rows))


# ============================================================
# Entry point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="51job cupid FAST crawler (WAF→exit)")
    parser.add_argument("--delay", type=float, default=FAST_DELAY)
    args = parser.parse_args()
    slow.DELAY_SEC = args.delay

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    logger = logging.getLogger("fast")
    logger.info("=" * 60)
    logger.info("51job Cupid FAST Crawler")
    logger.info("  Delay: %.1fs | PageSize: %d | Scout: ON", slow.DELAY_SEC, slow.PAGE_SIZE)
    logger.info("  WAF → save + exit (you switch IP, re-run)")
    logger.info("=" * 60)

    crawl()


if __name__ == "__main__":
    main()
