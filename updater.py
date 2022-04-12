#!/usr/bin/env python3
"""
Command line tool that fills the database
"""

import argparse
import logging
from datetime import datetime, timedelta, timezone

from pdpyras import APISession
from sqlalchemy import asc, create_engine
from sqlalchemy.orm import sessionmaker

from config import PD_API_TOKEN, PD_TEAMS, RW_DB_STRING
from models import Alert, Base, Incident

logger = logging.getLogger(__name__)

MAX_UPDATE_ATTEMPTS = 30


class IncidentCacheUpdater:
    """
    Caches incidents from the PD API
    """

    def __init__(self, pd_session, db_session, team_ids, limit, since=None, until=None):
        """
        Creates an instance of IncidentCacheUpdater focused on a particular time window

        :param pd_api_session: a PagerDuty API token string.
        :param db_session: an SQLAlchemy session
        :param team_ids: a list of PagerDuty team IDs (usually short alphanumeric
            strings). Only incidents belonging to these teams will be cached.
        :param limit: maximum number of incidents to request from PD API
        :param since: a datetime.datetime containing the start of the time window.
            Defaults to 30 days ago.
        :param until: a datetime.datetime containing the start of the time window.
            Defaults to now.
        """
        self.pd_session = pd_session
        self.team_ids = team_ids
        self.limit = limit
        self.db_session = db_session

        if not since:
            # Default time window start to 30d ago
            since = datetime.now(timezone.utc) - timedelta(days=30)
        self.since = since

        if not until:
            # Default time window end to now
            until = datetime.now(timezone.utc)
        self.until = until

    def update_incidents(self):
        """
        Long-running method that pulls incidents from the PD API and uploads them to our
        caching database

        :returns: list of updated incidents
        """
        pd_params = {
            "team_ids[]": self.team_ids,
            "limit": self.limit if self.limit < 100 else 100,
            "sort_by": "created_at:desc",
        }
        if self.since:
            pd_params["since"] = self.since.isoformat()
        if self.until:
            pd_params["until"] = self.until.isoformat()

        cached_incidents = []
        for inc_dict in self.pd_session.iter_all("incidents", params=pd_params):
            # pylint: disable=broad-except
            try:
                inc = Incident.from_pd_api_response(self.db_session, inc_dict)
                inc_log_dict = self.pd_session.rget(
                    f"incidents/{inc.pd_id}/log_entries"
                )
                inc.populate_via_api_log(self.db_session, inc_log_dict)
                self.db_session.add(inc)
            except Exception as exc:
                logger.exception(
                    "Failed to process incident %s", inc_dict["id"], exc_info=exc
                )
            else:
                cached_incidents.append(inc)
                logger.info("Cached %s", inc)
                if len(cached_incidents) >= self.limit:
                    break

        return cached_incidents


class AlertCacheUpdater:
    """
    s
    """

    def __init__(self, pd_session, db_session, incident_list):
        """
        Creates an instance of AlertCacheUpdater focused on a set of incidents

        :param pd_api_session: a PagerDuty API session.
        :param db_session: an SQLAlchemy session
        :param incident_list: list of incidents for which to cache associated alerts
        """
        self.pd_session = pd_session
        self.db_session = db_session
        self.incident_list = incident_list

    def update_alerts(self):
        """
        Long-running method that pulls alerts from the PD API that correspond either to
        the incidents just cached (if calling after update_incidents()) or to any
        incidents in the database that have a created_at timestamp between self.since
        and self.until.

        :returns: the number of newly-cached alerts
        """
        alert_count = 0
        for inc in self.incident_list:
            logger.debug("Querying alerts owned by %s", inc)
            for alert_dict in self.pd_session.iter_all(f"incidents/{inc.pd_id}/alerts"):
                # pylint: disable=broad-except
                try:
                    alert = Alert.from_pd_api_response(
                        session=self.db_session, res_dict=alert_dict
                    )
                    self.db_session.add(alert)
                except Exception as exc:
                    logger.exception(
                        "Failed to process alert %s", alert_dict["id"], exc_info=exc
                    )
                else:
                    logger.info("Cached %s (belongs to %s)", alert, inc)
                    alert_count += 1
        return alert_count


def oldest_incident_ctime(db_session):
    """
    Returns the value of the created_at field of the oldest incident in the cache

    :param db_session: an SQLAlchemy session
    :returns: a datetime representing the creation time of the oldest incident, or
      datetime.max if the incident cache is empty
    """
    try:
        return (
            db_session.query(Incident)
            .order_by(asc(Incident.created_at))
            .filter(Incident.created_at is not None)
            .first()
            .created_at.replace(tzinfo=timezone.utc)
        )
    except AttributeError as exc:
        logger.warning("Incident cache is empty!")
        logger.debug("Exception details", exc_info=exc)
        return datetime.max.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    # pylint: disable=invalid-name

    # Set up argument parser
    parser = argparse.ArgumentParser(
        description=(
            "Updates the OSD alert-analysis tool's cache of PagerDuty"
            " incidents and alerts"
        )
    )
    parser.add_argument(
        "-s",
        "--since",
        type=str,
        help="start of the caching time window in ISO-8601 format (default: 30d ago)",
    )
    parser.add_argument(
        "-u",
        "--until",
        type=str,
        help="end of the caching time window in ISO-8601 format (default: now)",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        help="maximum number of incidents to cache (default: 10000)",
        default=10000,
    )
    parser.add_argument(
        "-b",
        "--backfill",
        metavar=" DAYS",
        type=int,
        help=(
            "do a normal run, then check to see if the oldest record in the cache is at"
            " least DAYS days old. if it's not, update cache until it is, batching in"
            " sizes of LIMIT if necessary. --since and --until have no effect after the"
            " initial run."
        ),
        default=0,
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)

    # Parse arguments
    args = parser.parse_args()
    LOG_LEVEL = logging.WARNING
    DB_ECHO = False
    if args.verbose == 1:
        LOG_LEVEL = logging.INFO
    elif args.verbose == 2:
        LOG_LEVEL = logging.DEBUG
    elif args.verbose == 3:
        LOG_LEVEL = logging.DEBUG
        DB_ECHO = True
    elif args.verbose >= 4:
        LOG_LEVEL = logging.DEBUG
        DB_ECHO = "debug"

    SINCE = UNTIL = None
    if args.since:
        SINCE = datetime.fromisoformat(args.since)
    if args.until:
        UNTIL = datetime.fromisoformat(args.until)
    BACKFILL_TARGET = datetime.now(timezone.utc) - timedelta(days=args.backfill)

    # Set up logging
    logging.basicConfig(level=LOG_LEVEL)

    # Set up DB connection
    engine = create_engine(RW_DB_STRING, echo=DB_ECHO, future=True)
    Base.metadata.create_all(engine)
    DBSession = sessionmaker(bind=engine)

    # Begin cache update
    logger.debug(
        "Starting cache update: since=%r, until=%r, team_ids=%r, limit=%d, backfill=%d",
        SINCE,
        UNTIL,
        PD_TEAMS,
        args.limit,
        args.backfill,
    )
    with DBSession() as db_sess, APISession(PD_API_TOKEN) as pd_sess:
        update_attempts_remaining = MAX_UPDATE_ATTEMPTS
        with db_sess.begin():
            while update_attempts_remaining > 0:
                update_attempts_remaining -= 1
                fresh_incidents = []
                print(
                    "Updating incident cache...",
                    end="" if LOG_LEVEL >= logging.WARNING else "\n",
                    flush=True,
                )
                icu = IncidentCacheUpdater(
                    pd_session=pd_sess,
                    db_session=db_sess,
                    team_ids=PD_TEAMS,
                    limit=args.limit,
                    since=SINCE,
                    until=UNTIL,
                )
                fresh_incidents = icu.update_incidents()
                print(f"done. Cached {len(fresh_incidents)} incidents.")

                print(
                    "Updating alert cache...",
                    end="" if LOG_LEVEL >= logging.WARNING else "\n",
                    flush=True,
                )
                acu = AlertCacheUpdater(
                    pd_session=pd_sess,
                    db_session=db_sess,
                    incident_list=fresh_incidents,
                )
                cached_alert_count = acu.update_alerts()
                print(f"done. Cached {cached_alert_count} alerts.")

                oldest_inc_ctime = oldest_incident_ctime(db_sess)
                if oldest_inc_ctime > BACKFILL_TARGET:
                    # Backfill required, so prepare for next while loop iteration
                    # Set SINCE to slightly before BACKFILL_TARGET to prevent chattering
                    SINCE = BACKFILL_TARGET - timedelta(days=1)
                    UNTIL = oldest_inc_ctime
                    print(f"Attempting to backfill {SINCE} to {UNTIL}")

                    logger.debug("%d attempts remaining", update_attempts_remaining)

                    if update_attempts_remaining <= 0:
                        logger.warning(
                            "Ran out of attempts without meeting backfill target (%s"
                            " short). Ensure incidents exist throughout the requested"
                            " time window, then try again with a larger --limit value"
                            " or smaller --backfill value",
                            oldest_inc_ctime - BACKFILL_TARGET,
                        )
                else:
                    # No backfill required, allow while loop to exit
                    update_attempts_remaining = 0

        logger.info("Database update complete!")
