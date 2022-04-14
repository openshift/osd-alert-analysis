"""
Unit tests for SQLAlchemy models
"""
import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# pylint: disable=import-error
from models import (
    Alert,
    Base,
    Incident,
    PDAgent,
    PDTeam,
)


class SQLAlchemyTestMixin:
    """
    Mix-in for unit tests covering SQL Alchemy models. Inherit this in addition to (and
    before!) unittest.testcase, and extend/override methods as needed with super()
    """

    # pylint: disable=invalid-name
    def setUp(self):
        """
        Pre-test set up code for SQLAlchemy models
        """
        self.engine = create_engine("sqlite:///:memory:", echo=True, future=True)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        Base.metadata.create_all(self.engine)

        with open("tests/sample_incident.json", encoding="UTF-8") as f:
            self.sample_incident = json.load(f)
        with open("tests/sample_incident_log.json", encoding="UTF-8") as f:
            self.sample_incident_log = json.load(f)
        with open("tests/sample_incident_alerts.json", encoding="UTF-8") as f:
            self.sample_incident_alerts = json.load(f)

    def tearDown(self):
        """
        Post-test tear down code for SQLAlchemy models
        """
        Base.metadata.drop_all(self.engine)


class TestIncident(SQLAlchemyTestMixin, unittest.TestCase):
    """
    Unit tests for models.Incident
    """

    def setUp(self):
        """
        Pre-test set up code for models.Incident

        :extends: SQLAlchemyTestMixin.setUp
        """
        super().setUp()

        inc = Incident.from_pd_api_response(
            session=self.session, res_dict=self.sample_incident
        )
        self.session.add(inc)
        self.session.commit()

        self.inc_id = inc.id

    def test_from_pd_api_response(self):
        """
        Tests classmethod from_pd_api_response (called in setUp())
        """
        inc = self.session.get(Incident, self.inc_id)
        # Test basic field extraction
        self.assertEqual(inc.pd_id, self.sample_incident["id"])
        self.assertEqual(inc.html_url, self.sample_incident["html_url"])
        self.assertEqual(inc.name, self.sample_incident["summary"])
        self.assertEqual(inc.status, self.sample_incident["status"])
        self.assertEqual(inc.service, self.sample_incident["service"]["summary"])
        self.assertEqual(inc.urgency, self.sample_incident["urgency"])

        # Test slightly-less-basic field extraction
        sample_esc_policy_str = (
            f"{self.sample_incident['escalation_policy']['summary']}"
            f" ({self.sample_incident['escalation_policy']['id']})"
        )
        self.assertEqual(inc.esc_policy, sample_esc_policy_str)

        # Test timestamps (cached_at, created_at)
        self.assertGreaterEqual(inc.cached_at, datetime.now() - timedelta(minutes=3))
        sample_created_at = datetime.fromisoformat(
            self.sample_incident["created_at"].replace("Z", "")
        )
        self.assertEqual(inc.created_at, sample_created_at)

        # resolved_at shouldn't be set yet
        self.assertIsNone(inc.resolved_at)

        # Test team creation
        for sample_team in self.sample_incident["teams"]:
            team_under_test = (
                self.session.query(PDTeam)
                .filter(PDTeam.pd_id.is_(sample_team["id"]))
                .one()
            )
            self.assertEqual(team_under_test.name, sample_team["summary"])
            self.assertEqual(team_under_test.html_url, sample_team["html_url"])

    def test_populate_via_api_log(self):
        """
        Test function populate_via_api_log()
        """
        inc = self.session.get(Incident, self.inc_id)
        inc.populate_via_api_log(self.session, self.sample_incident_log)
        self.session.add(inc)
        self.session.commit()

        # Test resolved_by
        sample_resolution_event = list(
            x for x in self.sample_incident_log if x["type"] == "resolve_log_entry"
        )[0]
        self.assertEqual(
            inc.resolved_by.pd_id,
            sample_resolution_event["agent"]["id"],
        )

        # Test resolved_at
        sample_resolved_at = datetime.fromisoformat(
            sample_resolution_event["created_at"].replace("Z", "")
        )
        self.assertEqual(inc.resolved_at, sample_resolved_at)

        # Test acknowledgements
        sample_ack_dicts = [
            x for x in self.sample_incident_log if x["type"] == "acknowledge_log_entry"
        ]
        self.assertEqual(len(inc.acknowledged_by), len(sample_ack_dicts))
        for sample_ack_dict in sample_ack_dicts:
            sample_ack_agent = (
                self.session.query(PDAgent)
                .filter(PDAgent.pd_id.is_(sample_ack_dict["agent"]["id"]))
                .one()
            )
            self.assertIn(sample_ack_agent, inc.acknowledged_by)

        # Test assigned_to
        sample_assignee_dicts = [
            item
            for sublist in [
                x["assignees"]
                for x in self.sample_incident_log
                if x["type"] == "assign_log_entry"
            ]
            for item in sublist
        ]
        self.assertEqual(len(inc.assigned_to), len(sample_assignee_dicts))
        for sample_assignee_dict in sample_assignee_dicts:
            sample_assignee = (
                self.session.query(PDAgent)
                .filter(PDAgent.pd_id.is_(sample_assignee_dict["id"]))
                .one()
            )
            self.assertIn(sample_assignee, inc.assigned_to)

    def test_updated_incident(self):
        """
        Tests change of object attributes over time (e.g., due to incident resolution)
        """
        updated_sample_incident = deepcopy(self.sample_incident)
        updated_sample_incident["urgency"] = "low"
        updated_sample_incident_log = deepcopy(self.sample_incident_log)
        new_log_entry = {
            "id": "R0KBF7IVQX9D1UY5SBQL9D0K8P",
            "type": "assign_log_entry",
            "summary": "Assigned to John Doe",
            "self": "https://example.com/log_entries/R0KBF7IVQX9D1UY5SBQL9D0K8P",
            "html_url": None,
            "created_at": "2022-02-15T19:20:53Z",
            "agent": {
                "id": "PA88GTF",
                "type": "user_reference",
                "summary": "Jane Doe",
                "self": "https://example.com/users/PA88GTF",
                "html_url": "https://example.com/users/PA88GTF",
            },
            "channel": {"type": "website"},
            "service": {
                "id": "PSLIKU3",
                "type": "service_reference",
                "summary": "osd-cpaas-ci.w7mj.p1.openshiftapps.com-hive-cluster",
                "self": "https://api.pagerduty.com/services/PSLIKU3",
                "html_url": "https://redhat.pagerduty.com/service-directory/PSLIKU3",
            },
            "incident": {
                "id": "Q3S9S0XH37Q37M",
                "type": "incident_reference",
                "summary": "[#868037] [See SNow ticket in Notes] DNSErrors05MinSRE CRITICAL (6)",
                "self": "https://api.pagerduty.com/incidents/Q3S9S0XH37Q37M",
                "html_url": "https://redhat.pagerduty.com/incidents/Q3S9S0XH37Q37M",
            },
            "teams": [
                {
                    "id": "PASPK4G",
                    "type": "team_reference",
                    "summary": "Platform SRE",
                    "self": "https://api.pagerduty.com/teams/PASPK4G",
                    "html_url": "https://redhat.pagerduty.com/teams/PASPK4G",
                }
            ],
            "contexts": [],
            "assignees": [
                {
                    "id": "XXQS1TQ",
                    "type": "user_reference",
                    "summary": "John Doe",
                    "self": "https://example.com/users/XXQS1TQ",
                    "html_url": "https://example.com/users/XXQS1TQ",
                }
            ],
        }
        updated_sample_incident_log.append(new_log_entry)

        updated_incident = Incident.from_pd_api_response(
            session=self.session, res_dict=updated_sample_incident
        )
        updated_incident.populate_via_api_log(self.session, updated_sample_incident_log)
        self.session.add(updated_incident)
        self.session.commit()
        self.session.flush()

        # Now re-get from database
        incident_under_test = self.session.get(Incident, self.inc_id)
        self.assertEqual(incident_under_test.pd_id, updated_sample_incident["id"])
        self.assertEqual(incident_under_test.urgency, "low")

        # Verify new assignment
        new_assignee = (
            self.session.query(PDAgent).filter(PDAgent.pd_id.is_("XXQS1TQ")).one()
        )
        self.assertIn(new_assignee, incident_under_test.assigned_to)

        # Verify number of assignees
        updated_sample_assignee_dicts = [
            item
            for sublist in [
                x["assignees"]
                for x in updated_sample_incident_log
                if x["type"] == "assign_log_entry"
            ]
            for item in sublist
        ]
        self.assertEqual(
            len(incident_under_test.assigned_to), len(updated_sample_assignee_dicts)
        )

        # Now just verify that there's no duplicate entries
        self.assertEqual(
            len(
                self.session.query(Incident)
                .filter(Incident.pd_id.is_(updated_sample_incident["id"]))
                .all()
            ),
            1,
        )

    def test_silenced(self):
        """
        Test the "silenced" event-listened column
        """
        inc = self.session.get(Incident, self.inc_id)
        self.assertFalse(inc.silenced)
        silent_test = PDAgent(name="Silent Test")
        inc.assigned_to.append(silent_test)
        self.assertTrue(inc.silenced)
        inc.assigned_to.remove(silent_test)
        self.assertFalse(inc.silenced)


class TestAlert(SQLAlchemyTestMixin, unittest.TestCase):
    """
    Unit tests for models.Alert
    """

    def setUp(self):
        """
        Pre-test set up code for models.Alert

        :extends: SQLAlchemyTestMixin.setUp
        """
        super().setUp()

        # Must create the parent incident before creating the alert
        inc = Incident.from_pd_api_response(
            session=self.session, res_dict=self.sample_incident
        )
        self.session.add(inc)
        self.session.commit()

        self.sample_alert = self.sample_incident_alerts[0]
        alert = Alert.from_pd_api_response(
            session=self.session, res_dict=self.sample_alert
        )
        self.session.add(alert)
        self.session.commit()

        self.alert_id = alert.id

    def test_from_pd_api_response(self):
        """
        Tests classmethod from_pd_api_response (called in setUp())
        """
        alert = self.session.get(Alert, self.alert_id)
        # Test basic field extraction
        self.assertEqual(alert.pd_id, self.sample_alert["id"])
        self.assertEqual(alert.html_url, self.sample_alert["html_url"])
        self.assertEqual(alert.severity, self.sample_alert["severity"])
        self.assertEqual(alert.suppressed, self.sample_alert["suppressed"])
        self.assertEqual(alert.status, self.sample_alert["status"])
        self.assertEqual(alert.name, self.sample_alert["body"]["details"]["alert_name"])
        self.assertEqual(
            alert.cluster_id, self.sample_alert["body"]["details"]["cluster_id"]
        )
        self.assertIn(alert.namespace, self.sample_alert["body"]["details"]["firing"])

        # Test incident relationship
        self.assertEqual(alert.incident.pd_id, self.sample_alert["incident"]["id"])

        # Test timestamps (cached_at, created_at, shift)
        self.assertGreaterEqual(alert.cached_at, datetime.now() - timedelta(minutes=3))
        sample_created_at = datetime.fromisoformat(
            self.sample_alert["created_at"].replace("Z", "")
        )
        self.assertEqual(alert.created_at, sample_created_at)
        sample_shift = Alert.calculate_shift(sample_created_at)
        self.assertEqual(alert.shift, sample_shift)
        ## resolved_at disabled as it seems to be an undocumented API field
        # sample_resolved_at = datetime.fromisoformat(
        #     self.sample_alert["resolved_at"].replace("Z", "")
        # )
        # self.assertEqual(alert.resolved_at, sample_resolved_at)

    def test_updated_alert(self):
        """
        Tests change of object attributes over time (e.g., due to alert resolution)
        """
        updated_sample_alert = deepcopy(self.sample_alert)
        updated_sample_alert["status"] = "triggered"
        updated_alert = Alert.from_pd_api_response(
            session=self.session, res_dict=updated_sample_alert
        )
        self.session.add(updated_alert)
        self.session.commit()
        self.session.flush()

        # Now re-get from database
        alert_under_test = self.session.get(Alert, self.alert_id)
        self.assertEqual(alert_under_test.pd_id, updated_sample_alert["id"])
        self.assertEqual(alert_under_test.status, "triggered")

        # Now just verify that there's no duplicate entries
        self.assertEqual(
            len(
                self.session.query(Alert)
                .filter(Alert.pd_id.is_(updated_sample_alert["id"]))
                .all()
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
