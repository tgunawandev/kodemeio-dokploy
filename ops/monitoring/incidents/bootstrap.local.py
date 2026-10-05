"""Seed only the isolated local pilot; execute through manage.py shell."""

import json
import os

from apps.alerts.models import AlertRecipient, ProjectAlert
from apps.api_tokens.models import APIToken
from apps.organizations_ext.models import Organization, OrganizationUser
from apps.projects.models import Project, ProjectKey
from apps.teams.models import Team
from django.contrib.auth import get_user_model

user, _ = get_user_model().objects.get_or_create(email="incident-pilot@example.invalid")
if not user.has_usable_password():
    user.set_unusable_password()
    user.save()
org, _ = Organization.objects.get_or_create(slug="incident-pilot", defaults={"name": "incident-pilot"})
member, _ = OrganizationUser.objects.get_or_create(organization=org, user=user, defaults={"role": 0})
project, _ = Project.objects.get_or_create(
    organization=org, slug="kodemeio-web-local", defaults={"name": "kodemeio-web-local", "platform": "python"}
)
team, _ = Team.objects.get_or_create(organization=org, slug="incident-pilot")
team.members.add(member)
team.projects.add(project)
token, _ = APIToken.objects.get_or_create(
    token=os.environ["INCIDENT_GLITCHTIP_READ_TOKEN"],
    defaults={"user": user, "label": "local incident collector read-only"},
)
token.scopes = 0
token.add_permissions(["org:read", "project:read", "event:read"])
operator, _ = get_user_model().objects.get_or_create(email="operator-local@example.invalid")
operator.set_password(os.environ["PILOT_OPERATOR_PASSWORD"])
operator.save()
OrganizationUser.objects.update_or_create(organization=org, user=operator, defaults={"role": 3})
alert, _ = ProjectAlert.objects.get_or_create(
    project=project, name="Local VERONICA pilot", defaults={"quantity": 1, "timespan_minutes": 1}
)
AlertRecipient.objects.get_or_create(
    alert=alert,
    recipient_type="webhook",
    url=f"http://incident-ingress:8080/webhooks/glitchtip/kodemeio-web-local/{os.environ['INCIDENT_GLITCHTIP_TOKEN']}",
)
key = ProjectKey.objects.filter(project=project).first()
canary, _ = Project.objects.get_or_create(
    organization=org, slug="next-sdk-local", defaults={"name": "next-sdk-local", "platform": "javascript-nextjs"}
)
team.projects.add(canary)
canary_alert, _ = ProjectAlert.objects.get_or_create(
    project=canary, name="Local Next SDK canary", defaults={"quantity": 1, "timespan_minutes": 1}
)
AlertRecipient.objects.get_or_create(
    alert=canary_alert,
    recipient_type="webhook",
    url=f"http://incident-ingress:8080/webhooks/glitchtip/next-sdk-local/{os.environ['INCIDENT_GLITCHTIP_TOKEN']}",
)
canary_key = ProjectKey.objects.filter(project=canary).first()
print(
    json.dumps(
        {
            "project_id": project.pk,
            "public_key": str(key.public_key),
            "canary_project_id": canary.pk,
            "canary_public_key": canary_key.public_key.hex,
        }
    )
)
