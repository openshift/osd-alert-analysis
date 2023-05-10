"""
Database Models
"""
import re
import datetime
from logging import getLogger

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.event import listens_for
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func as sqlfunc
from sqlalchemy.ext.hybrid import hybrid_property

# Force use of the space-saving 3-byte UTF8 charset instead of the default 4-byte one
SQL_CHARSET = "utf8mb3"

Base = declarative_base()

logger = getLogger(__name__)

# BEGIN SQLAlchemy association tables for many-to-many relationships
inc_assignments = Table(
    "incident_assignments",
    Base.metadata,
    Column("incident_id", ForeignKey("incidents.id"), primary_key=True),
    Column("pdagent_id", ForeignKey("pd_agents.id"), primary_key=True),
    mariadb_charset=SQL_CHARSET,
    mysql_charset=SQL_CHARSET,
)

inc_acknowledgements = Table(
    "incident_acknowledgements",
    Base.metadata,
    Column("incident_id", ForeignKey("incidents.id"), primary_key=True),
    Column("pdagent_id", ForeignKey("pd_agents.id"), primary_key=True),
    mariadb_charset=SQL_CHARSET,
    mysql_charset=SQL_CHARSET,
)

inc_teams = Table(
    "incident_teams",
    Base.metadata,
    Column("incident_id", ForeignKey("incidents.id"), primary_key=True),
    Column("pdteam_id", ForeignKey("pd_teams.id"), primary_key=True),
    mariadb_charset=SQL_CHARSET,
    mysql_charset=SQL_CHARSET,
)
# END association tables


class PDEntityMixin:
    """
    A "mixin" class that can be inherited from to provide a set of fields and class
    methods common to PagerDuty entities
    """

    __table_args__ = {"mariadb_charset": SQL_CHARSET, "mysql_charset": SQL_CHARSET}
    id = Column(Integer, primary_key=True)
    pd_id = Column(String(length=31), unique=True)
    # pylint: disable=not-callable
    cached_at = Column(
        TIMESTAMP, server_default=sqlfunc.now(), onupdate=sqlfunc.current_timestamp()
    )
    html_url = Column(String(length=511))
    name = Column(String(length=511))

    @classmethod
    def get_or_create(cls, session, pd_id, name, html_url):
        """
        Get the requested PagerDuty entity from the database, or create it if it doesn't
        already exist, and adds it to the current session

        :param session: an SQLAlchemy session object
        :param pd_id: the all-caps alphanumeric PagerDuty ID of the requested entity
        :param name: human-readable name of the entity (PD often calls this "summary")
        :param html_url: the URL to the PagerDuty webUI's representation of the entity
        :returns: the requested entity object
        """
        name = name[:511] if name else None
        html_url = html_url[:511] if html_url else None
        instance = session.query(cls).filter_by(pd_id=pd_id).first()
        if instance:
            # instance found: update fields
            instance.name = name
            instance.html_url = html_url
            logger.debug("Updating existing record %s", instance)
        else:
            # instance not found: create it
            instance = cls(pd_id=pd_id, html_url=html_url, name=name)
            logger.debug("Creating new record %s", instance)

        # Add the created/updated object to this session
        session.add(instance)

        # if instance in session.dirty:
        #     session.commit()

        return instance

    @classmethod
    def from_pd_api_response(cls, session, res_dict):
        """
        Helper method: runs get_or_create() on a PagerDuty API response dict

        :param session: an SQLAlchemy session object
        :param res_dict: PD API response dict with keys "id", "summary", and "html_url"
        :returns: the requested entity object from get_or_create()
        """
        return cls.get_or_create(
            session=session,
            pd_id=res_dict["id"],
            name=res_dict["summary"],
            html_url=res_dict["html_url"],
        )

    def __repr__(self) -> str:
        """
        Human readable representation
        """
        return f"<{self.__class__.__name__} {self.pd_id}>"


class PDTeam(PDEntityMixin, Base):
    """
    SQLAlchemy model for PagerDuty team entities
    """

    __tablename__ = "pd_teams"


class PDAgent(PDEntityMixin, Base):
    """
    SQLAlchemy model for PagerDuty user/agent entities. Note that not all "users" on
    PagerDuty are actual people. Other than "Silent Test," the PD API will also
    occasionally consider services (clusters) to be "users"
    """

    __tablename__ = "pd_agents"


class Incident(PDEntityMixin, Base):
    """
    SQLAlchemy model for PagerDuty incident entities
    """

    __tablename__ = "incidents"
    created_at = Column(DateTime())
    esc_policy = Column(String(length=255))
    teams = relationship("PDTeam", secondary=inc_teams)
    service = Column(String(length=255))
    status = Column(Enum("triggered", "acknowledged", "resolved"))
    urgency = Column(Enum("low", "high"))
    assigned_to = relationship("PDAgent", secondary=inc_assignments)
    acknowledged_by = relationship("PDAgent", secondary=inc_acknowledgements)
    resolved_at = Column(DateTime())
    resolved_by_id = Column(Integer, ForeignKey("pd_agents.id"))
    resolved_by = relationship("PDAgent")
    silenced = Column(Boolean(), default=False)

    @hybrid_property
    def service_name(self):
        """
        SQLAlchemy hybrid property that returns a human-readable version of the service name
        """
        return sqlfunc.substring_index(Incident.service, ".", 2)

    def populate_via_api_log(self, session, pd_log_entries):
        """
        Certain Incident fields (assigned_to, acknowledged_by, resolved_at, and
        resolved_by) aren't included in PagerDuty's responses to its /incidents API
        endpoint. This method takes the result from a call to the
        /incidents/{id}/log_entries API endpoint and fills-in those fields.

        :param session: an SQLAlchemy session object
        :param pd_log_entries: a list of (or iterator over) the events associated with
            the incident
        :returns: Nothing (populates fields in-place)
        """
        for entry in pd_log_entries:
            if entry["type"] == "resolve_log_entry":
                self.resolved_at = datetime.datetime.fromisoformat(
                    entry["created_at"].replace("Z", "+00:00")
                )
                self.resolved_by = PDAgent.from_pd_api_response(session, entry["agent"])
                logger.debug("Found resolution by %s", self.resolved_by)
            elif entry["type"] == "acknowledge_log_entry":
                # Warning: must assign newly-created PDAgent to var here, or else
                # Python garbage collector may invalidate it before it's committed
                new_agent = PDAgent.from_pd_api_response(session, entry["agent"])
                self.acknowledged_by.append(new_agent)
                logger.debug("Found acknowledgement by %s", new_agent)
            elif entry["type"] == "assign_log_entry":
                for assignee in entry["assignees"]:
                    # See GC warning above re. new_agent
                    new_agent = PDAgent.from_pd_api_response(session, assignee)
                    self.assigned_to.append(new_agent)
                    logger.debug("Found assignment to %s", new_agent)

    @classmethod
    def from_pd_api_response(cls, session, res_dict):
        """
        Creates or updates instance of an Incident using the dict returned by the PD
        API's /incidents endpoint. It's recommended to call populate_via_api_log() on
        the returned Incident in order to fill in all fields.

        :extends: PDEntityMixin.from_pd_api_response
        :param session: an SQLAlchemy session object
        :param res_dict: the dict returned by the PD API for the Incident being created
        :returns: an Incident ORM object suitable for storage in the database
        """
        inc = super(Incident, cls).from_pd_api_response(session, res_dict)
        inc.created_at = datetime.datetime.fromisoformat(
            res_dict["created_at"].replace("Z", "+00:00")
        )
        try:
            inc.esc_policy = (
                f"{res_dict['escalation_policy']['summary']}"
                f" ({res_dict['escalation_policy']['id']})"
            )
        except KeyError as exc:
            logger.warning("%s's escalation policy is missing or invalid", inc)
            logger.debug("Exception details", exc_info=exc)
        inc.service = res_dict["service"]["summary"][:255]
        inc.status = res_dict["status"]
        inc.urgency = res_dict["urgency"]

        inc.teams = [PDTeam.from_pd_api_response(session, x) for x in res_dict["teams"]]

        return inc


# pylint: disable=unused-argument
@listens_for(Incident.assigned_to, "set")
def _set_assigned_to(target, value, oldvalue, initiator):
    """
    Incident setter event listener: checks if Silent Test is being assigned and updates
    silenced field accordingly
    """
    silenced = False
    for entity in value:
        if "silent test" in str(entity.name).lower():
            silenced = True
    target.silenced = silenced


@listens_for(Incident.assigned_to, "append")
def _append_assigned_to(parent, child, initiator):
    """
    Incident append event listener: checks if Silent Test is being assigned and updates
    silenced field accordingly
    """
    if "silent test" in str(child.name).lower():
        parent.silenced = True


@listens_for(Incident.assigned_to, "remove")
def _remove_assigned_to(parent, child, initiator):
    """
    Incident remove event listener: checks if Silent Test is being unassigned and updates
    silenced field accordingly
    """
    if "silent test" in str(child.name).lower():
        parent.silenced = False


class Alert(PDEntityMixin, Base):
    """
    SQLAlchemy model for PagerDuty incident entities
    """

    __tablename__ = "alerts"
    created_at = Column(DateTime())
    incident_id = Column(Integer, ForeignKey("incidents.id"))
    incident = relationship("Incident")
    status = Column(Enum("triggered", "resolved"))
    severity = Column(Enum("info", "warning", "error", "critical"))
    suppressed = Column(Boolean(), default=False)
    cluster_id = Column(String(length=40))
    # shift indicates which region was on-call during the creation of this alert
    shift = Column(String(length=31))
    # namespace comes from the firing data, and is something like "openshift-monitoring"
    namespace = Column(String(length=255))
    # We set firing_details' max-length to a very large number to force allocation of a
    # MEDIUMTEXT column, which supports up to 2^24 characters (or bytes; docs unclear)
    firing_details = Column(Text(length=4000000))
    ## resolved_at disabled as it seems to be an undocumented API field
    # resolved_at = Column(DateTime())

    @classmethod
    def calculate_shift(cls, date_time):
        """
        Class method for mapping a UTC-based datetime.datetime to an OSD on-call shift
        """
        # Borrowed from Benjamin Dematteo (https://github.com/bdematte/pd-stats-python)
        date = date_time.date()

        if date_time.hour < 3 or (date_time.hour == 3 and date_time.minute < 30):
            shift = "APAC 1"
        elif date_time.hour < 8 or (date_time.hour == 8 and date_time.minute < 30):
            shift = "APAC 2"
        elif date_time.hour < 13 or (date_time.hour == 13 and date_time.minute < 30):
            shift = "EMEA"
        elif date_time.hour < 18:
            shift = "NASA 1"
        elif date_time.hour < 22 or (date_time.hour == 22 and date_time.minute < 30):
            shift = "NASA 2"
        else:
            # End of UTC day is covered by APAC 1 (next working day)
            shift = "APAC 1"

        return f"{shift} ({date})"

    @classmethod
    def standardize_name(cls, raw_name):
        """
        Standardizes alert names, abbreviating them when necessary

        :param raw_name: the input alert name string
        :returns: the standardized alert name
        :raises: TypeError if raw_name is None, empty, or not a string
        """
        try:
            if "ClusterProvisioningDelay" in raw_name:
                return "ClusterProvisioningDelay"

            if "has gone missing" in raw_name:
                return "ClusterHasGoneMissing"

            if "Heartbeat.ping has failed" in raw_name:
                return "HeartbeatPingFailed"

            if "CUST ESCALATION" in raw_name:
                return "CustomerEscalation"
            # Catch-all: take the first word and limit it under column max_length
            return raw_name.split()[0][:500]

        except (AttributeError, IndexError, TypeError) as exc:
            raise TypeError("Failed to standardize alert name") from exc

    @classmethod
    def from_pd_api_response(cls, session, res_dict):
        """
        Creates new instance of an Alert using one of the dicts returned by the
        PagerDuty API's /incidents/{id}/alerts endpoint.

        :extends: PDEntityMixin.from_pd_api_response
        :param session: an SQLAlchemy session object
        :param res_dict: the dict returned by the PD API for the Alert being created
        :returns: returns an Alert ORM object suitable for storage in the database
        """
        alert = super(Alert, cls).from_pd_api_response(session, res_dict)

        # Set simple, PD-API-guaranteed fields
        alert.status = res_dict["status"]
        alert.severity = res_dict["severity"]
        alert.suppressed = res_dict["suppressed"]

        # Set created_at (shift will also be set by listener; see below)
        alert.created_at = datetime.datetime.fromisoformat(
            res_dict["created_at"].replace("Z", "+00:00")
        )

        # Link to incident
        alert.incident = (
            session.query(Incident)
            .filter(Incident.pd_id == res_dict["incident"]["id"])
            .one()
        )

        # Set name
        try:
            try:
                alert.name = cls.standardize_name(
                    res_dict["body"]["details"]["alert_name"]
                )
            except KeyError:
                # Some alerts don't have a standard details section (e.g., CHGM)
                alert.name = cls.standardize_name(res_dict["summary"])
        except TypeError as exc:
            logger.warning("%s's name is missing or invalid", alert)
            logger.debug("Exception details", exc_info=exc)

        # Set cluster ID
        try:
            cluster_id = res_dict["body"]["details"]["cluster_id"]
            alert.cluster_id = cluster_id[:40] if cluster_id else None
        except KeyError as exc:
            logger.warning("%s's cluster ID is missing or invalid", alert)
            logger.debug("Exception details", exc_info=exc)

        # Set firing details
        try:
            alert.firing_details = res_dict["body"]["details"]["firing"]
        except KeyError as exc:
            logger.warning("%s's firing details are missing or invalid", alert)
            logger.debug("Exception details", exc_info=exc)

        return alert


@listens_for(Alert.created_at, "set")
def _set_created_at(target, value, oldvalue, initiator):
    """
    Alert setter event listener: updates shift field upon created_at
    """
    target.shift = Alert.calculate_shift(value)


@listens_for(Alert.firing_details, "set")
def _set_firing_details(target, value, oldvalue, initiator):
    """
    Alert setter event listener: updates namespace field upon firing_details
    """
    namespace_re = re.search(r"namespace = (.*)\n", value)
    try:
        target.namespace = namespace_re.group(1)
    except (AttributeError, IndexError) as exc:
        logger.warning("%s's firing details are missing a namespace", target)
        logger.debug("Exception details", exc_info=exc)
