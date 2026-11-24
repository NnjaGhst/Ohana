"""Stage an approved reboot audiobook, verify deployment, or stage a rollback."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from pipeline import RATE, decode, read, require, sha, write

SERIES = "elsewhere-reboot-2026"
CATALOG = Path("stories/index.json")


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *map(str, args)], capture_output=True, check=True)
    return result.stdout


def clean_branch(root):
    branch = git(root, "branch", "--show-current").decode().strip()
    require(branch and branch != "main" and not git(root, "status", "--porcelain"),
            "Use a clean working branch separate from main")


def targets(episode_id):
    match = re.fullmatch(SERIES + r"-(\d{3})", episode_id)
    require(match is not None and int(match[1]) > 0, "Invalid reboot episode ID")
    base = Path("series/elsewhere/reboot-2026/episodes") / f"episode-{match[1]}"
    return base, int(match[1])


def entry(catalog, episode_id, number, base):
    matches = [item for item in catalog["episodes"] if item["id"] == episode_id]
    require(len(matches) == 1, "Expected exactly one intended catalog row")
    row = matches[0]
    require(row["seriesId"] == SERIES and row["number"] == number
            and row["metadata"] == (base / "ohana-metadata.json").as_posix(), "Wrong catalog episode")
    return row


def approved_master(output, record):
    report_file = output / "qa.json"
    report = read(report_file)
    require(report["status"] == "listening_required" and report["series_id"] == SERIES
            and not any(flag["severity"] != "review" for flag in report["flags"]), "QA has blocking findings")
    digest = report["master_sha256"]
    require(re.fullmatch(r"[a-f0-9]{64}", digest) and report["master"] == f"preview-{digest}.mp3",
            "Invalid versioned master")
    expected = {"episode_id": report["episode_id"], "master_sha256": digest, "qa_sha256": sha(report_file)}
    require(all(record[key] == value for key, value in expected.items()), "Approval is stale or for another master")
    require(record["owner"] == "Ninja" and record["approved"] is True and record["approval_reference"].strip(),
            "Explicit owner approval required")
    require(record["reviewer"].strip() and record["listened_to_flagged_passages"] is True
            and record["listened_to_representative_joins"] is True and record["review_notes"].strip(),
            "Listening review incomplete")
    require(set(record["resolved_review_flags"]) == set(range(len(report["flags"]))),
            "Unresolved listening flags")
    artifacts = report["artifacts"]
    require({"timeline.json", "mix.wav", "voice.wav", "environment.wav", "foley.wav"} <= artifacts.keys(),
            "QA artifacts missing")
    for name, checksum in artifacts.items():
        require(Path(name).name == name and sha(output / name) == checksum, "Changed QA artifact")
    master = output / report["master"]
    require(sha(master) == digest, "Master checksum mismatch")
    require(read(output / "timeline.json")["samples"] == len(decode(master)), "Master duration mismatch")
    return report, master


def stage(root, output, approval, receipt):
    clean_branch(root)
    require(not receipt.resolve().is_relative_to(root.resolve()) and not receipt.exists(),
            "Choose a new receipt path outside the checkout")
    record = read(approval)
    report, master = approved_master(output, record)
    episode_id, digest = report["episode_id"], report["master_sha256"]
    base, number = targets(episode_id)
    catalog = read(root / CATALOG)
    row = entry(catalog, episode_id, number, base)
    metadata = Path("stories") / row["metadata"]
    info = read(root / metadata)
    old_audio = row.get("audio")
    require(info["episode"] == number and info["series"] == row["seriesName"]
            and info.get("audio_file") == old_audio, "Catalog and metadata disagree")
    if old_audio:
        require(Path(old_audio).parent == base / "audio" and (root / "stories" / old_audio).is_file(),
                "Previous catalog asset missing or outside episode")
    audio = base / "audio" / f"episode-{number:03d}-{digest}.mp3"
    target = root / "stories" / audio
    require(not target.exists(), "Release requires a fresh filename")
    row["audio"] = audio.as_posix()
    row["status"] = "ready"
    info.update(audio_file=audio.as_posix(), status="ready", audio_preview_approved=True,
                published_to_ohana=True, actual_duration_seconds=read(output / "timeline.json")["samples"] / RATE)
    previous = git(root, "rev-parse", "HEAD").decode().strip()
    paths = (CATALOG, metadata, Path("stories") / audio)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(master, target)
        require(sha(target) == digest, "Copied master checksum mismatch")
        write(root / CATALOG, catalog)
        write(root / metadata, info)
        git(root, "add", "--", *paths)
        # Record the exact tree before committing so a failed receipt write cannot leave a release commit.
        release_tree = git(root, "write-tree").decode().strip()
        write(receipt, dict(episode_id=episode_id, metadata=metadata.as_posix(), audio=audio.as_posix(),
                            sha256=digest, previous_audio=old_audio, parent=previous, tree=release_tree,
                            approval=record))
        git(root, "commit", "-m", f"Stage approved audiobook {episode_id}")
    except Exception:
        receipt.unlink(missing_ok=True)
        git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", CATALOG, metadata)
        git(root, "reset", "-q", "HEAD", "--", paths[2])
        target.unlink(missing_ok=True)
        raise


def verify(root, receipt, observed, ref="origin/main"):
    base, number = targets(receipt["episode_id"])
    row = entry(json.loads(git(root, "show", f"{ref}:{CATALOG}")), receipt["episode_id"], number, base)
    metadata = Path("stories") / row["metadata"]
    info = json.loads(git(root, "show", f"{ref}:{metadata}"))
    audio = receipt["audio"]
    require(row["audio"] == audio and info["audio_file"] == audio and info["episode"] == number
            and info["series"] == row["seriesName"], "Stale catalog or wrong main metadata")
    require(hashlib.sha256(git(root, "show", f"{ref}:stories/{audio}")).hexdigest() == receipt["sha256"],
            "Main blob differs from approved master")
    require(observed["episode_id"] == receipt["episode_id"] and observed["series_id"] == SERIES
            and observed["catalog_audio"] == audio
            and all(urlsplit(observed[name]).scheme == "https" and urlsplit(observed[name]).hostname
                    for name in ("public_url", "ohana_url")),
            "Public URL or Ohana readback did not verify")
    for field in ("public_download", "ohana_download"):
        downloaded = Path(observed[field])
        require(downloaded.is_file() and sha(downloaded) == receipt["sha256"],
                "Public URL or Ohana readback audio differs from approved master")


def rollback(root, receipt):
    clean_branch(root)
    base, number = targets(receipt["episode_id"])
    catalog = read(root / CATALOG)
    row = entry(catalog, receipt["episode_id"], number, base)
    metadata = Path("stories") / row["metadata"]
    require(row.get("audio") == receipt["audio"] and read(root / metadata).get("audio_file") == receipt["audio"],
            "Current release differs from rollback target")
    old_catalog = json.loads(git(root, "show", f"{receipt['parent']}:{CATALOG}"))
    old_row = entry(old_catalog, receipt["episode_id"], number, base)
    old_info = json.loads(git(root, "show", f"{receipt['parent']}:{metadata}"))
    require(old_row.get("audio") == receipt["previous_audio"] == old_info.get("audio_file"),
            "Rollback references disagree")
    if receipt["previous_audio"]:
        require((root / "stories" / receipt["previous_audio"]).is_file(), "Rollback audio missing")
    current_info = read(root / metadata)
    for target, old, fields in ((row, old_row, ("audio", "status")),
                                (current_info, old_info, ("audio_file", "status", "audio_preview_approved",
                                                         "published_to_ohana", "actual_duration_seconds"))):
        for field in fields:
            if field in old:
                target[field] = old[field]
            else:
                target.pop(field, None)
    write(root / CATALOG, catalog)
    write(root / metadata, current_info)
    git(root, "add", "--", CATALOG, metadata)
    git(root, "commit", "-m", f"Restore prior audiobook for {receipt['episode_id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, arguments in (("stage", ["repo", "output", "approval", "receipt"]),
                            ("verify", ["repo", "receipt", "readback"]),
                            ("rollback", ["repo", "receipt"])):
        command = commands.add_parser(name)
        for argument in arguments:
            command.add_argument(argument, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "stage":
            stage(args.repo, args.output, args.approval, args.receipt)
            print("Release commit staged on working branch. Deployment verification pending.")
        elif args.command == "verify":
            verify(args.repo, read(args.receipt), read(args.readback))
            print("Approved master verified against main, public URL and Ohana readback.")
        else:
            rollback(args.repo, read(args.receipt))
            print("Rollback staged on working branch. Prior audio assets retained.")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Release blocked ({type(error).__name__}): {error}\n")


if __name__ == "__main__":
    main()
