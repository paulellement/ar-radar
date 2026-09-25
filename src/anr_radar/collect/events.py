"""Daily pull of upcoming music events for the Shows feature.

Raw events land as JSONL. Their performers are also returned so the snapshot can add them
to the candidate pool: most artists playing small local rooms aren't on any Last.fm chart,
and they're exactly the ones the Shows tab is for.
"""

import argparse
import datetime as dt
import logging

from anr_radar import config
from anr_radar.clients.ticketmaster import TicketmasterClient
from anr_radar.keys import artist_key
from anr_radar.secrets import get_secret
from anr_radar.storage import partition_dir, read_jsonl, write_jsonl

log = logging.getLogger(__name__)
DATASET = "ticketmaster/events"


def events_path(root: str, snapshot_date: str):
    return partition_dir(root, DATASET, snapshot_date) / "part-0.jsonl"


def performers(events: list[dict]) -> list[dict]:
    """Distinct performers (Ticketmaster attractions) with their MusicBrainz ID if linked."""
    seen: dict[str, dict] = {}
    for e in events:
        city = e.get("city_query", "")
        for a in e.get("_embedded", {}).get("attractions", []):
            mb = (a.get("externalLinks") or {}).get("musicbrainz") or [{}]
            mbid = mb[0].get("id") or None
            entry = {
                "artist_key": artist_key(a["name"], mbid),
                "name": a["name"],
                "mbid": mbid,
                "source": f"event:{city}",
            }
            seen.setdefault(entry["artist_key"], entry)
    return list(seen.values())


def load_performers(root: str, snapshot_date: str) -> list[dict]:
    path = events_path(root, snapshot_date)
    if not path.exists():
        return []
    return performers([row["payload"] | {"city_query": row["city"]} for row in read_jsonl(path)])


def run(client: TicketmasterClient, root: str, now: dt.datetime) -> int:
    end = now + dt.timedelta(days=config.EVENT_HORIZON_DAYS)
    fetched_at = now.isoformat(timespec="seconds")
    rows = []
    for city, country in config.EVENT_CITIES:
        events = client.music_events(city, country, now, end)
        log.info("%s: %d upcoming music events", city, len(events))
        rows += [
            {
                "source": "ticketmaster.events",
                "snapshot_date": now.date().isoformat(),
                "fetched_at": fetched_at,
                "city": city,
                "country": country,
                "payload": e,
            }
            for e in events
        ]
    return write_jsonl(events_path(root, now.date().isoformat()), rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pull upcoming music events")
    parser.add_argument("--root", default=config.DEFAULT_LANDING_ROOT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = TicketmasterClient(
        get_secret("TICKETMASTER_API_KEY", "ticketmaster_key"),
        requests_per_sec=config.TICKETMASTER_REQUESTS_PER_SEC,
    )
    run(client, args.root, dt.datetime.now(dt.UTC).replace(microsecond=0))


if __name__ == "__main__":
    main()
