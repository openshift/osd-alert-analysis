"""
Questions models
"""
import re
from sqlalchemy import desc
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import text

from models import Alert, Incident, PDAgent

STANDARD_COLUMNS = ["name", "namespace", "urgency", "silenced", "occurrences"]


class Answer:
    """
    Container class for the results of Questions
    """

    def __init__(self, question_id, column_names, raw_data) -> None:
        """
        Sets answer column and data values
        """
        self._question_id = question_id
        self._column_names = column_names
        self._raw_data = raw_data

        safe_column_names = [
            re.sub(r"[^\d\w]+", "_", cn.strip()) for cn in self._column_names
        ]
        self._column_ids = [f"{scn}_{question_id}" for scn in safe_column_names]

    @property
    def columns(self):
        """
        Returns a Dash DataTable-friendly representation of the columns for this answer
        """
        return list(
            {"name": cn, "id": cid}
            for cn, cid in zip(self._column_names, self._column_ids)
        )

    @property
    def data(self):
        """
        Returns a Dash DataTable-friendly representation of the data for this answer
        """
        return list(dict(zip(self._column_ids, row)) for row in self._raw_data)


class Question:
    """
    Base class for questions
    """

    def __init__(self, db_session, since, until):
        """
        Constructor for Questions. Mainly sets up DB connection

        :param db_session: an SQLAlchemy session
        :param since: a datetime.datetime containing the start of the time window over
            which the question query will be evaluated
        :param until: a datetime.datetime containing the end of the time window over
            which the question query will be evaluated
        """
        self._since = since
        self._until = until
        self._db_session = db_session

        self._column_names = STANDARD_COLUMNS.copy()

        # The following should be defined in child class constructors
        self._id = ""
        self._description = ""

    @classmethod
    def _aggregate_by_alert(cls, alert_analyzer_query):
        """
        Takes the raw query returned by an SQLAlchemy query and does a value
        count/aggregation, returning a table with columns [name, namespace, urgency,
        silenced, occurrences]

        :returns: an SQLAlchemy result object
        """
        alert_count = func.count("*").label("occurrences")
        return (
            alert_analyzer_query.join(Incident)
            .group_by(Alert.name, Alert.namespace, Incident.urgency, Incident.silenced)
            .with_entities(
                Alert.name,
                Alert.namespace,
                Incident.urgency,
                Incident.silenced,
                alert_count,
            )
            .having(alert_count > 1)
            .order_by(desc(alert_count))
            .all()
        )

    def _query(self):
        """
        Returns the SQLAlchemy query used to answer this Question
        """
        raise NotImplementedError

    def get_answer(self):
        """
        Returns an Answer to the question
        """
        raw_data = self._aggregate_by_alert(self._query())
        return Answer(self._id, self._column_names, raw_data)

    # pylint: disable=invalid-name
    @property
    def id(self) -> str:
        """
        Get the machine-readable ID of the question
        """
        return self._id

    @property
    def description(self) -> str:
        """
        Get the human-readable description of this Question
        """
        return self._description

    def __str__(self) -> str:
        """
        Human-readable string representation
        """
        return self._description

    def __repr__(self) -> str:
        """
        Unambiguous machine-oriented representation
        """
        return self._id


class QNeverAcknowledged(Question):
    """
    Which alerts have yet to be acknowledged by SRE?
    """

    def __init__(self, db_session, since, until):
        super().__init__(db_session, since, until)
        self._id = "nack"
        self._description = "Which alerts have yet to be acknowledged by SRE?"

    def _query(self):
        # The ~ operator negates the condition
        return (
            self._db_session.query(Alert)
            .filter(Alert.created_at.between(self._since, self._until))
            .filter(Alert.incident.has(~Incident.acknowledged_by.any()))
        )


class QNeverAcknowledgedSelfResolved(Question):
    """
    Which alerts self-resolve without acknowledgement?
    """

    def __init__(self, db_session, since, until):
        super().__init__(db_session, since, until)
        self._id = "nacksres"
        self._description = "Which alerts self-resolve without acknowledgement?"

    def _query(self):
        # The ~ operator negates the condition
        return (
            self._db_session.query(Alert)
            .filter(Alert.created_at.between(self._since, self._until))
            .filter(Alert.incident.has(~Incident.acknowledged_by.any()))
            .filter(
                Alert.incident.has(
                    Incident.resolved_by.has(PDAgent.name.contains("Alertmanager"))
                )
            )
        )


class QAcknowledgedUnresolved(Question):
    """
    Which alerts are acknowledged but never resolved?
    """

    def __init__(self, db_session, since, until):
        super().__init__(db_session, since, until)
        self._id = "ackures"
        self._description = "Which alerts are acknowledged but never resolved?"

    def _query(self):
        # pylint: disable=singleton-comparison
        return (
            self._db_session.query(Alert)
            .filter(Alert.created_at.between(self._since, self._until))
            .filter(Alert.incident.has(Incident.acknowledged_by.any()))
            .filter(Alert.incident.has(Incident.resolved_at == None))
        )


class QSelfResolvedImmediately(Question):
    """
    Which alerts self-resolve w/in 15 minutes?
    """

    def __init__(self, db_session, since, until):
        super().__init__(db_session, since, until)
        self._id = "sres15"
        self._description = "Which alerts self-resolve within 15 minutes?"

    def _query(self):
        # pylint: disable=singleton-comparison
        return (
            self._db_session.query(Alert)
            .filter(Alert.created_at.between(self._since, self._until))
            .filter(~Alert.incident.has(Incident.resolved_at == None))
            .filter(
                Alert.incident.has(
                    Incident.resolved_by.has(PDAgent.name.contains("Alertmanager"))
                )
            )
            .filter(
                Alert.incident.has(
                    Incident.resolved_at
                    < func.date_add(Incident.created_at, text("INTERVAL 15 MINUTE"))
                )
            )
        )


class QSREResolvedImmediately(Question):
    """
    Which alerts are resolved by SRE within 15 minutes of firing?
    """

    def __init__(self, db_session, since, until):
        super().__init__(db_session, since, until)
        self._id = "eres15"
        self._description = "Which alerts are resolved within 15 minutes by SRE?"

    def _query(self):
        # pylint: disable=singleton-comparison
        return (
            self._db_session.query(Alert)
            .filter(Alert.created_at.between(self._since, self._until))
            .filter(~Alert.incident.has(Incident.resolved_at == None))
            .filter(
                ~Alert.incident.has(
                    Incident.resolved_by.has(PDAgent.name.contains("Alertmanager"))
                )
            )
            .filter(
                Alert.incident.has(
                    Incident.resolved_at
                    < func.date_add(Incident.created_at, text("INTERVAL 15 MINUTE"))
                )
            )
        )


class QFlappingShift(Question):
    """
    Which alerts fire more than once per on-call shift (in the same cluster)?
    """

    def __init__(self, db_session, since, until):
        super().__init__(db_session, since, until)
        self._id = "sflap"
        self._description = (
            "Which alerts fire more than once per on-call shift (in the same cluster)?"
        )
        self._column_names = ["cluster", "name", "namespace", "urgency", "flaps"]

    def _query(self):
        # First create a subquery that counts flaps per-shift-date
        flap_count = func.count("*").label("flap_count")
        subq = (
            self._db_session.query(Alert)
            .filter(Alert.created_at.between(self._since, self._until))
            .join(Incident)
            .group_by(
                Alert.cluster_id,
                Alert.name,
                Alert.namespace,
                Alert.shift,
                Incident.urgency,
            )
            .with_entities(
                Alert.cluster_id,
                Alert.name,
                Alert.namespace,
                Incident.urgency,
                flap_count,
            )
            .having(flap_count > 1)
        ).subquery()

        # Then add up the flaps for alerts that flapped on multiple shift-days
        flap_sum = func.sum(subq.c.flap_count).label("flap_sum")
        return (
            self._db_session.query(subq, flap_sum)
            .group_by(subq.c.cluster_id, subq.c.name, subq.c.namespace, subq.c.urgency)
            .order_by(desc(flap_sum))
        )

    def get_answer(self):
        return Answer(self._id, self._column_names, self._query().all())
