from litestar import Request
from litestar.exceptions import NotAuthorizedException

from zonegate.authorization.console import SESSION_COOKIE, ConsoleAuthService
from zonegate.domain.actors import Actor


async def require_console_actor(request: Request, console_auth: ConsoleAuthService) -> Actor:
    """The authority signed in to the console, or 401.

    Every console action that changes what the engine decides, who is on the
    roster, or how a hold ends goes through here. The mobile app never calls
    these endpoints, so requiring a console session costs the field nothing.
    """
    actor = await console_auth.resolve(request.cookies.get(SESSION_COOKIE))
    if actor is None:
        raise NotAuthorizedException(detail="Sign in to the console to do this")
    return actor
