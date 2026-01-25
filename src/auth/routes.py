from flask import Blueprint, current_app, session, redirect, url_for, jsonify
from flask import request
from authlib.integrations.base_client.errors import MismatchingStateError
import secrets

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/auth/google/login')
def google_login():
    oauth = getattr(current_app, 'oauth', None)
    if oauth is None:
        return jsonify({'error': 'OAuth not configured'}), 500
    # Generate a deterministic state, store it explicitly in the session,
    # and pass it to the provider. Some deployments/clients can lose the
    # automatic session-backed state, so storing our own copy improves
    # reliability during local development.
    state = secrets.token_urlsafe(16)
    try:
        session['oauth_state'] = state
        session['state'] = state
        session['authlib_state'] = state
        session['google_oauth_state'] = state
    except Exception:
        current_app.logger.debug('failed to write state into session')

    try:
        current_app.logger.debug('google_login session keys: %s', list(session.keys()))
    except Exception:
        current_app.logger.debug('google_login session logging failed')

    redirect_uri = url_for('auth.google_callback', _external=True)
    prompt = request.args.get('prompt')
    current_app.logger.debug('google_login redirect_uri=%s state=%s prompt=%s', redirect_uri, state, prompt)
    if prompt:
        return oauth.google.authorize_redirect(redirect_uri, state=state, prompt=prompt)
    return oauth.google.authorize_redirect(redirect_uri, state=state)


@auth_bp.route('/auth/google/callback')
def google_callback():
    oauth = getattr(current_app, 'oauth', None)
    if oauth is None:
        return jsonify({'error': 'OAuth not configured'}), 500
    # Log incoming request args and current session for debugging
    try:
        current_app.logger.debug('google_callback request.args: %s', dict(request.args))
    except Exception:
        current_app.logger.debug('google_callback args logging failed')
    try:
        current_app.logger.debug('google_callback session keys: %s', list(session.keys()))
    except Exception:
        current_app.logger.debug('google_callback session logging failed')

    try:
        token = oauth.google.authorize_access_token()
    except MismatchingStateError:
        # State mismatch (CSRF) — log and restart the login flow instead of
        # crashing with a traceback. This commonly indicates the session
        # cookie (where the state is stored) wasn't present on callback.
        current_app.logger.warning('OAuth state mismatch in callback; request.args=%s session_keys=%s',
                                   dict(request.args), list(session.keys() if session is not None else []))
        return redirect(url_for('auth.google_login'))
    # Try to parse ID token (OpenID Connect). If not available, fetch userinfo endpoint.
    userinfo = None
    try:
        userinfo = oauth.google.parse_id_token(token)
    except Exception:
        try:
            # Known Google userinfo endpoint (fallback)
            resp = oauth.google.get('https://openidconnect.googleapis.com/v1/userinfo')
            userinfo = resp.json()
        except Exception:
            return jsonify({'error': 'failed to fetch userinfo'}), 500
    # minimal profile
    profile = {
        'sub': userinfo.get('sub'),
        'email': userinfo.get('email'),
        'name': userinfo.get('name'),
        'picture': userinfo.get('picture'),
    }
    session['user'] = profile
    # Redirect to frontend home
    return redirect('/frontend/')


@auth_bp.route('/auth/logout')
def logout():
    # Clear local session and send the user to a public signed-out page.
    # We avoid immediately starting the Google login flow here because if
    # the user's Google session is still active they'll be reauthenticated
    # immediately and sent back into the app. The `logged_out` page lets
    # the user choose to sign in again (or sign out of Google in the browser).
    session.pop('user', None)
    return redirect('/logged_out')


@auth_bp.route('/auth/me')
def me():
    user = session.get('user')
    if not user:
        return jsonify({'authenticated': False}), 401
    return jsonify({'authenticated': True, 'user': user})


@auth_bp.route('/auth/debug_session')
def debug_session():
    # Temporary debug endpoint: return session keys and simple string
    # representations of values. DO NOT enable in production.
    try:
        data = {k: (str(v)[:100] + '...' if isinstance(v, (str, bytes)) and len(str(v)) > 100 else str(v)) for k, v in session.items()}
    except Exception:
        data = {'error': 'failed to serialize session'}
    current_app.logger.debug('debug_session keys: %s', list(session.keys()))
    return jsonify({'session_keys': list(session.keys()), 'session_preview': data})
