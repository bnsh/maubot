# maubot - A plugin-based Matrix bot system.
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from json import JSONDecodeError

from aiohttp import web

from mautrix.types import UserID, SyncToken, FilterID
from mautrix.errors import MatrixRequestError, MatrixInvalidToken
from mautrix.client import Client as MatrixClient

from ...db import DBClient
from ...client import Client
from .base import routes
from .responses import (RespDeleted, ErrClientNotFound, ErrBodyNotJSON, ErrClientInUse,
                        ErrBadClientAccessToken, ErrBadClientAccessDetails, ErrMXIDMismatch,
                        ErrUserExists)


@routes.get("/clients")
async def get_clients(_: web.Request) -> web.Response:
    return web.json_response([client.to_dict() for client in Client.cache.values()])


@routes.get("/client/{id}")
async def get_client(request: web.Request) -> web.Response:
    user_id = request.match_info.get("id", None)
    client = Client.get(user_id, None)
    if not client:
        return ErrClientNotFound
    return web.json_response(client.to_dict())


async def create_client(user_id: UserID, data: dict) -> web.Response:
    homeserver = data.get("homeserver", None)
    access_token = data.get("access_token", None)
    new_client = MatrixClient(base_url=homeserver, token=access_token, loop=Client.loop,
                              client_session=Client.http_client)
    try:
        mxid = await new_client.whoami()
    except MatrixInvalidToken:
        return ErrBadClientAccessToken
    except MatrixRequestError:
        return ErrBadClientAccessDetails
    if user_id == "new":
        existing_client = Client.get(mxid, None)
        if existing_client is not None:
            return ErrUserExists
    elif mxid != user_id:
        return ErrMXIDMismatch
    db_instance = DBClient(id=user_id, homeserver=homeserver, access_token=access_token,
                           enabled=data.get("enabled", True), next_batch=SyncToken(""),
                           filter_id=FilterID(""), sync=data.get("sync", True),
                           autojoin=data.get("autojoin", True),
                           displayname=data.get("displayname", ""),
                           avatar_url=data.get("avatar_url", ""))
    client = Client(db_instance)
    Client.db.add(db_instance)
    Client.db.commit()
    await client.start()
    return web.json_response(client.to_dict())


async def update_client(client: Client, data: dict) -> web.Response:
    try:
        await client.update_access_details(data.get("access_token", None),
                                           data.get("homeserver", None))
    except MatrixInvalidToken:
        return ErrBadClientAccessToken
    except MatrixRequestError:
        return ErrBadClientAccessDetails
    except ValueError:
        return ErrMXIDMismatch
    await client.update_avatar_url(data.get("avatar_url", None))
    await client.update_displayname(data.get("displayname", None))
    await client.update_started(data.get("started", None))
    client.enabled = data.get("enabled", client.enabled)
    client.autojoin = data.get("autojoin", client.autojoin)
    client.sync = data.get("sync", client.sync)
    return web.json_response(client.to_dict())


@routes.put("/client/{id}")
async def update_client(request: web.Request) -> web.Response:
    user_id = request.match_info.get("id", None)
    # /client/new always creates a new client
    client = Client.get(user_id, None) if user_id != "new" else None
    try:
        data = await request.json()
    except JSONDecodeError:
        return ErrBodyNotJSON
    if not client:
        return await create_client(user_id, data)
    else:
        return await update_client(client, data)


@routes.delete("/client/{id}")
async def delete_client(request: web.Request) -> web.Response:
    user_id = request.match_info.get("id", None)
    client = Client.get(user_id, None)
    if not client:
        return ErrClientNotFound
    if len(client.references) > 0:
        return ErrClientInUse
    if client.started:
        await client.stop()
    client.delete()
    return RespDeleted
