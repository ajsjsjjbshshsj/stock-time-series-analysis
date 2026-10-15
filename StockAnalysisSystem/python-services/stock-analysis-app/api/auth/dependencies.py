"""Authoritative backend checks; frontend role claims are never consumed."""
import ipaddress
from fastapi import HTTPException


def service(request):
    result=request.app.state.auth_service
    if result is None: raise HTTPException(503,'Authentication dependency unavailable')
    return result


def token(request): return request.cookies.get(request.app.state.auth_settings.cookie)


def client_ip(request):
    peer=request.client.host if request.client else 'unknown'
    try:
        address=ipaddress.ip_address(peer)
        if any(address in ipaddress.ip_network(net) for net in request.app.state.auth_settings.trusted_proxies):
            headers=request.headers.getlist('x-auth-client-ip')
            if len(headers)==1: return str(ipaddress.ip_address(headers[0]))
    except ValueError: pass
    return peer


def require_user(request,*,allow_password_change=False):
    try: current=service(request).session(token(request))
    except HTTPException: raise
    except Exception: raise HTTPException(503,'Authentication dependency unavailable') from None
    if current is None: raise HTTPException(401,'Authentication required')
    if current['must_change_password'] and not allow_password_change:
        raise HTTPException(403,'Password change required')
    return current


def require_admin(request):
    current=require_user(request)
    if current['role']!='admin': raise HTTPException(403,'Access denied')
    return current


def require_csrf(request):
    origins=request.headers.getlist('origin'); values=request.headers.getlist('x-csrf-token')
    settings=request.app.state.auth_settings
    if len(origins)!=1 or origins[0]!=settings.origin or len(values)!=1:
        raise HTTPException(403,'Request origin or CSRF invalid')
    try: valid=service(request).validate_csrf(token(request),values[0])
    except HTTPException: raise
    except Exception: raise HTTPException(503,'Authentication dependency unavailable') from None
    if not valid: raise HTTPException(403,'Request origin or CSRF invalid')
