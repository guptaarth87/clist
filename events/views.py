from collections import Counter

import humanfriendly
from django.db.models import Q, Prefetch
from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import render, get_object_or_404, redirect, resolve_url
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.mail import EmailMessage
from django.views.decorators.clickjacking import xframe_options_exempt
from django.utils.timezone import now, timedelta
from django.template.loader import get_template
from django_countries import countries
from django.views.decorators.cache import cache_page
from phonenumber_field.phonenumber import PhoneNumber
from el_pagination.decorators import page_templates

import true_coders.views
from events.models import Event, Participant, Team, JoinRequest, TeamStatus, TshirtSize
from clist.views import get_timezone, get_timeformat
from clist.models import Resource
from true_coders.models import Organization
from ranking.models import Account, Module
from utils.regex import get_iregex_filter


def events(request):
    return render(
        request,
        'events.html',
        {
            'events': Event.objects.order_by('-created'),
        },
    )


@page_templates((
    ('team-participants.html', 'teams'),
    ('participants.html', 'participants'),
    ('team-participants-admin.html', 'teams-admin'),
))
def event(request, slug, tab=None, template='event.html', extra_context=None):
    event = get_object_or_404(Event, slug=slug)
    if tab is not None and tab not in ['information', 'registration', 'teams', 'participants', 'admin']:
        return HttpResponseNotFound()
    user = request.user
    coder = None if user.is_anonymous else user.coder
    participant = event.participant_set.filter(coder=coder, coder__isnull=False).first()
    if participant:
        join_requests = participant.joinrequest_set.all()
        team = event.team_set \
            .filter(participants__pk=participant.pk) \
            .prefetch_related('joinrequest_set', 'participants') \
            .first()
    else:
        join_requests = None
        team = None

    end_registration = event.registration_deadline < now()
    registration_timeleft = event.registration_deadline - now()
    if coder and coder.settings.get('unlimited_deadline'):
        registration_timeleft = timedelta(minutes=42)
        end_registration = False

    query = request.POST.get('query') or request.GET.get('query')
    if query is not None and not end_registration and coder:
        if query == 'skip-coach':
            if not team or team.author != participant:
                messages.error(request, 'First you need to create a team')
            elif team.status != TeamStatus.ADD_COACH:
                messages.error(request, 'Not necessary skip coach')
            else:
                team.status = TeamStatus.PENDING
                team.save()
        elif query in ['join', 'add-coach']:  # 'join-as-coach'
            is_coach = query == 'add-coach'
            ok = True
            if not is_coach and participant:
                messages.info(request, 'You have already joined the event')
                ok = False
            if is_coach and (not team or team.status != TeamStatus.ADD_COACH):
                messages.error(request, 'First you need to create a team')
                ok = False
            else:
                active_fields = [
                    'first-name',
                    'last-name',
                    'email',
                    'first-name-native',
                    'last-name-native',
                    'phone-number',
                    'tshirt-size',
                ]
                if not is_coach:
                    active_fields.append('organization')
                    active_fields.append('country')
                for field in active_fields:
                    if not request.POST.get(field, ''):
                        messages.error(request, 'You must specify all the information')
                        ok = False
                        break
                else:
                    if not is_coach and not coder.token_set.filter(email=request.POST['email']).exists():
                        messages.error(request, 'Invalid email. Check that the account with this email is connected')
                        ok = False
                active_fields.append('middle-name-native')

                try:
                    phone_number = PhoneNumber.from_string(phone_number=request.POST.get('phone-number'))
                    assert phone_number.is_valid()
                except Exception:
                    messages.error(request, 'Invalid phone number')
                    ok = False

                handle = request.POST.get('codeforces-handle')
                error = None
                if handle:
                    resource = Resource.objects.get(host='codeforces.com')
                    account = Account.objects.filter(key=handle, resource=resource).first()
                    if not account:
                        error = f'Codeforces handle {handle} not found'
                    elif coder.account_set.filter(resource=resource).exists():
                        error = f'Codeforces handle {handle} is already connect'
                    else:
                        module = Module.objects.filter(resource=resource).first()
                        if not module or not module.multi_account_allowed:
                            if coder.account_set.filter(resource=resource).exists():
                                error = 'Allow only one account for this resource'
                            elif account.coders.count():
                                error = 'Account is already connect'
                        account.coders.add(coder)
                        account.save()
                if error:
                    messages.error(request, error)
                    ok = False
            if ok:
                if is_coach:
                    participant = Participant.objects.create(event=event, is_coach=True)
                else:
                    participant, _ = Participant.objects.get_or_create(coder=coder, event=event)

                created = False

                try:
                    data = dict(list(request.POST.items()))
                    data['phone-number'] = phone_number.as_e164
                    for field in active_fields:
                        if data.get(field):
                            data[field] = data[field].strip()

                    if 'organization' in data:
                        organization_name = data['organization']
                        organization, created = Organization.objects.get_or_create(name=organization_name)
                        if created:
                            organization.name_ru = organization_name
                            organization.author = coder
                            organization.save()
                        data['organization'] = organization

                    for object_, attr in (
                        (user, 'first_name'),
                        (user, 'last_name'),
                        (user, 'email'),
                        (coder, 'first_name_native'),
                        (coder, 'last_name_native'),
                        (coder, 'middle_name_native'),
                        (coder, 'phone_number'),
                        (coder, 'country'),
                        (coder, 'organization'),
                        (None, 'tshirt_size'),
                    ):
                        field = attr.replace('_', '-')
                        if field not in active_fields:
                            continue
                        value = data[field]
                        if not is_coach and object_:
                            setattr(object_, attr, value or getattr(object_, attr))
                        setattr(participant, attr, value)
                    participant.save()
                    user.save()
                    coder.save()
                    if is_coach:
                        team.coach = participant
                        team.status = TeamStatus.PENDING
                        team.save()
                except Exception:
                    participant.delete()
                    if created:
                        organization.delete()
                    raise
        elif query == 'create-team':
            team_name = request.POST.get('team')
            if not team_name:
                messages.error(request, 'You must specify team name')
            elif len(team_name) > 42:
                messages.error(request, 'The team name is too long (limit is 42)')
            elif participant and participant.is_coach:
                team = Team.objects.create(event=event, name=team_name, author=participant)
                team.coach = participant
                team.save()
            elif team or join_requests:
                messages.error(request, 'You have already committed an action')
            else:
                team = Team.objects.create(event=event, name=team_name, author=participant)
                team.participants.add(participant)
                team.save()
        elif query == 'join-team':
            is_coach = participant and participant.is_coach
            if not is_coach and (team or join_requests):
                messages.error(request, 'You have already committed an action')
            else:
                team_id = int(request.POST.get('team'))
                team = Team.objects.get(pk=team_id, event=event, status=TeamStatus.NEW)
                if not JoinRequest.objects.filter(team=team, participant=participant).exists():
                    JoinRequest.objects.create(team=team, participant=participant)
        elif query == 'cancel-request':
            if not join_requests:
                messages.error(request, 'Join request not found')
            else:
                join_request_id = int(request.GET.get('request_id'))
                JoinRequest.objects.filter(pk=join_request_id, participant=participant).delete()
        elif query in ['accept-team', 'reject-team']:
            request_id = int(request.POST.get('request_id'))
            join_request = JoinRequest.objects \
                .filter(pk=request_id) \
                .filter(Q(team__author=participant) | Q(team__coach=participant)) \
                .first()
            if not join_request:
                messages.error(request, 'First you need to create a join request')
            else:
                participant = join_request.participant
                team = join_request.team
                if query == 'accept-team':
                    if participant.is_coach:
                        team.coach = participant
                        team.save()
                    else:
                        team.participants.add(participant)
                join_request.delete()
        elif query in ['register', 'delete-team']:
            team_id = int(request.POST.get('team_id'))
            team = Team.objects \
                .filter(pk=team_id) \
                .filter(Q(author=participant) | Q(coach=participant)) \
                .first()
            if not team:
                messages.error(request, 'First you need to create a team')
            elif team.status != TeamStatus.NEW:
                messages.error(request, 'Team already registered')
            elif query == 'delete-team':
                team.delete()
            elif not 0 < team.participants.count() < 4:
                messages.error(request, 'Team should be consists of one to three participants')
            else:
                if team.coach:
                    team.status = TeamStatus.PENDING
                else:
                    team.status = TeamStatus.ADD_COACH
                team.save()

        return redirect(resolve_url('events:event-tab', slug=event.slug, tab='registration'))

    teams = Team.objects.filter(event=event).order_by('-created')
    teams = teams.prefetch_related(
        'participants__coder__user',
        'participants__organization',
    )
    teams = teams.select_related(
        'author__coder__user',
        'author__organization',
        'coach__coder__user',
        'coach__organization',
    )
    codeforces_resource = Resource.objects.get(host='codeforces.com')
    teams = teams.prefetch_related(
        Prefetch(
            'participants__coder__account_set',
            queryset=Account.objects.filter(resource=codeforces_resource),
        ),
        'participants__coder__account_set__resource',
    )

    approved_statuses = {k for k, v in TeamStatus.descriptions.items() if v == 'approved'}
    team_search = request.GET.get('team_search')
    if team_search:
        team_search_filter = get_iregex_filter(
            team_search,
            'name',
            'participants__first_name',
            'participants__last_name',
            'participants__organization__name',
            'participants__organization__abbreviation',
            'coach__first_name',
            'coach__last_name',
            'coach__organization__name',
            'coach__organization__abbreviation',
        )
        teams = teams.filter(team_search_filter)
    team_participants = teams.filter(status__in=approved_statuses)

    participants = Participant.objects.filter(
        event=event,
        is_coach=False,
    ).filter(
        Q(team=None) | Q(team__status=TeamStatus.NEW),
    )
    participant_search = request.GET.get('participant_search')
    if participant_search:
        participant_search_filter = get_iregex_filter(
            participant_search,
            'first_name',
            'last_name',
            'organization__name',
            'organization__abbreviation',
            'team__name',
        )
        participants = participants.filter(participant_search_filter)

    status = request.GET.get('status')
    if status is not None:
        teams = teams.filter(status=status)

    context = {
        'slug': slug,
        'event': event,
        'tab': tab,
        'svg_r': 5,
        'coder': coder,
        'participant': participant,
        'team_participants': team_participants,
        'participants': participants,
        'team': team,
        'join_requests': join_requests,
        'team_status': TeamStatus,
        'tshirt_size': TshirtSize,
        'teams': teams,
        'timezone': get_timezone(request),
        'timeformat': get_timeformat(request),
        'defaults': {
            'country': {'code': 'BY', 'name': dict(countries)['BY']},
        },
        'registration': {
            'over': end_registration,
            'timeleft': humanfriendly.format_timespan(registration_timeleft),
        },
        'codeforces_resource': codeforces_resource,
    }

    if extra_context is not None:
        context.update(extra_context)

    return render(request, template, context)


def result(request, slug, name):
    event = get_object_or_404(Event, slug=slug)
    standings_urls = {s['name'].lower(): s['url'] for s in event.standings_urls}
    url = standings_urls[name]
    if url.startswith('http'):
        return redirect(url)
    template = get_template(url)
    html = template.render()
    return HttpResponse(html)


@permission_required('events.change_team')
def team_admin_view(request, slug, team_id):
    event = get_object_or_404(Event, slug=slug)
    team = get_object_or_404(Team, pk=team_id, event=event)

    if 'action' in request.POST:
        action = request.POST.get('action')
        if action == 'change':
            status = request.POST.get('status')
            team_name = request.POST.get('name')
            team.name = team_name
            team.status = int(status)
            team.save()
        elif action == 'email':
            subject = request.POST['subject']
            message = request.POST['message']
            emails = list(map(str.strip, request.POST['emails'].split(',')))
            with event.email_backend() as connection:
                EmailMessage(
                    subject,
                    message,
                    from_email=event.email_conf['from_email'],
                    to=emails,
                    connection=connection,
                ).send()
        return redirect('events:team-details', slug=slug, team_id=team_id)

    return render(
        request,
        'team-admin-view.html',
        {
            'slug': slug,
            'team': team,
            'event': event,
            'team_status': TeamStatus,
            'tshirt_size': TshirtSize,
        },
    )


@cache_page(1 if settings.DEBUG else 30 * 60)
@xframe_options_exempt
def frame(request, slug, status):
    event = get_object_or_404(Event, slug=slug)
    statuses = []
    if status == TeamStatus.labels[TeamStatus.SCHOOL_FINAL]:
        statuses.append(TeamStatus.SCHOOL_FINAL)
    elif status == TeamStatus.labels[TeamStatus.FINAL]:
        statuses.append(TeamStatus.INVITED)
        statuses.append(TeamStatus.FINAL)
    else:
        for s in (
            TeamStatus.INVITED,
            TeamStatus.FINAL,
            TeamStatus.SCHOOL_FINAL,
            TeamStatus.BSU_SEMIFINAL,
            TeamStatus.SEMIFINAL,
            TeamStatus.QUARTERFINAL,
            TeamStatus.QUALIFICATION,
            TeamStatus.EDITING,
            TeamStatus.DISQUALIFIED,
        ):
            statuses.append(s)
            if TeamStatus.labels[s] == status:
                break

    codeforces_resource = Resource.objects.get(host='codeforces.com')

    teams = Team.objects.filter(event=event, status__in=statuses).order_by('-created')
    teams = teams.prefetch_related(
        'participants__coder__user',
        'participants__organization',
        Prefetch(
            'participants__coder__account_set',
            queryset=Account.objects.filter(resource=codeforces_resource),
        ),
        'participants__coder__account_set__resource',
    )
    teams = teams.select_related(
        'author__coder__user',
        'author__organization',
        'coach__coder__user',
        'coach__organization',
    )
    countries = Counter(t.country for t in teams)

    base_css_path = staticfiles_storage.path('css/base.css')
    with open(base_css_path, 'r') as fo:
        base_css = fo.read()

    return render(
        request,
        'frame-team.html',
        {
            'teams': teams,
            'countries': countries.most_common(),
            'team_status': TeamStatus,
            'base_css': base_css,
            'codeforces_resource': codeforces_resource,
        },
    )


@login_required
def change(request, slug):
    return true_coders.views.change(request)


@login_required
def search(request, slug):
    event = get_object_or_404(Event, slug=slug)
    return true_coders.views.search(request, event=event)
