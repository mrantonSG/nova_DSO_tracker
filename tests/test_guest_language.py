"""Logged-out visitors share the guest_user account; their language choice must stay in their own session."""
import json
import re

from flask import g

from nova import app, _seed_user_from_guest_data
from nova.helpers import get_locale
from nova.models import DbUser, UiPref


def _html_lang(response):
    match = re.search(r'<html lang="([^"]*)"', response.get_data(as_text=True))
    assert match, "no <html lang> found"
    return match.group(1)


def _guest_saved_language(db_session):
    db_session.expire_all()
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    prefs = db_session.query(UiPref).filter_by(user_id=guest.id).first()
    if not prefs or not prefs.json_blob:
        return None
    return json.loads(prefs.json_blob).get('language')


def test_guest_language_pick_stays_in_session(mu_client_logged_out, db_session):
    response = mu_client_logged_out.get('/set_language/ja')
    assert response.status_code == 302

    assert _guest_saved_language(db_session) is None
    with mu_client_logged_out.session_transaction() as sess:
        assert sess.get('language') == 'ja'
    assert _html_lang(mu_client_logged_out.get('/login')) == 'ja'


def test_second_guest_unaffected(mu_client_logged_out, db_session):
    mu_client_logged_out.get('/set_language/ja')

    with app.test_client() as other_client:
        assert _html_lang(other_client.get('/login')) == 'en'


def test_guest_user_saved_language_is_ignored(mu_client_logged_out, db_session):
    mu_client_logged_out.get('/login')  # first request may seed guest_user's prefs
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    prefs = db_session.query(UiPref).filter_by(user_id=guest.id).first()
    if not prefs:
        prefs = UiPref(user_id=guest.id, json_blob='{}')
        db_session.add(prefs)
    settings = json.loads(prefs.json_blob or '{}')
    settings['language'] = 'ja'
    prefs.json_blob = json.dumps(settings)
    db_session.commit()

    assert _html_lang(mu_client_logged_out.get('/login')) == 'en'


def test_accept_language_unsupported_first_entry_falls_back_to_en(mu_client_logged_out):
    response = mu_client_logged_out.get('/login', headers={'Accept-Language': 'fr;q=0.1, xx'})
    assert _html_lang(response) == 'en'


def test_accept_language_region_maps_to_primary(mu_client_logged_out):
    response = mu_client_logged_out.get('/login', headers={'Accept-Language': 'de-AT,de;q=0.9'})
    assert _html_lang(response) == 'de'


def test_accept_language_missing_defaults_to_en(mu_client_logged_out):
    assert _html_lang(mu_client_logged_out.get('/login')) == 'en'


def test_get_locale_guest_rules():
    cases = [
        ({'Accept-Language': 'fr;q=0.1, xx'}, 'en'),
        ({'Accept-Language': 'de-AT,de;q=0.9'}, 'de'),
        ({}, 'en'),
    ]
    for headers, expected in cases:
        with app.test_request_context('/login', headers=headers):
            g.is_guest = True
            g.user_config = {'language': 'ja'}
            assert get_locale() == expected


def test_new_user_does_not_inherit_guest_language(db_session):
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    db_session.add(UiPref(user_id=guest.id, json_blob=json.dumps({'language': 'ja', 'theme_preference': 'dark'})))
    new_user = DbUser(username="newbie")
    db_session.add(new_user)
    db_session.commit()

    _seed_user_from_guest_data(db_session, new_user)
    db_session.commit()

    prefs = db_session.query(UiPref).filter_by(user_id=new_user.id).one()
    settings = json.loads(prefs.json_blob)
    assert 'language' not in settings
    assert settings['theme_preference'] == 'dark'


def test_logout_sets_nova_lang_cookie(multi_user_client, db_session):
    client, ids = multi_user_client
    db_session.add(UiPref(user_id=ids["user_a_id"], json_blob=json.dumps({'language': 'de'})))
    db_session.commit()

    response = client.post('/logout')
    assert response.status_code == 302

    cookies = [h for h in response.headers.getlist('Set-Cookie') if h.startswith('nova_lang=')]
    assert cookies, "logout did not set nova_lang"
    assert cookies[0].split(';', 1)[0] == 'nova_lang=de'


def test_guest_login_page_uses_nova_lang_cookie(mu_client_logged_out):
    mu_client_logged_out.set_cookie('nova_lang', 'en')
    response = mu_client_logged_out.get('/login', headers={'Accept-Language': 'de-DE,de;q=0.9'})
    assert _html_lang(response) == 'en'
