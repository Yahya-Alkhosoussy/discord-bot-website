import os
import secrets

import requests
from dotenv import load_dotenv
from flask import Blueprint, redirect, render_template, request, session, url_for

from twitch_database_interaction import (
    add_bot_commands,
    add_user,
    change_activity,
    delete_command,
    edit_specific_command,
    get_bot_commands,
    get_bot_id,
    get_bot_twitch_channels,
    get_bots,
    get_specific_command,
    get_user,
    is_user_in,
    save_twitch_token,
)

load_dotenv()

twitch_bp = Blueprint("twitch", "twitch", url_prefix="/bot-management/twitch")

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI")


@twitch_bp.route("/login")
def login():
    state = secrets.token_urlsafe(32)
    session["twitch_oauth_state"] = state
    return redirect(
        "https://id.twitch.tv/oauth2/authorize"
        f"?client_id={TWITCH_CLIENT_ID}"
        f"&redirect_uri={TWITCH_REDIRECT_URI}"
        "&response_type=code"
        "&scope=user:read:email+user:read:moderated_channels"
        f"&state={state}"
    )


@twitch_bp.route("/auth/callback")
async def callback():
    code = request.args.get("code")

    # exchange code for acces token
    token_response = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": TWITCH_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    token_data = token_response.json()
    if "access_token" not in token_data:
        return redirect(url_for("index"))

    token = token_data["access_token"]
    user_response = requests.get(
        "https://api.twitch.tv/helix/users",
        headers={
            "Authorization": f"Bearer {token}",
            "Client-Id": TWITCH_CLIENT_ID,
        },
    ).json()

    user = user_response["data"][0]

    moderated = requests.get(
        "https://api.twitch.tv/helix/moderation/channels",
        params={"user_id": user["id"]},
        headers={"Authorization": f"Bearer {token}", "Client-Id": TWITCH_CLIENT_ID},
    ).json()

    moderated_channels = [
        {"id": ch["broadcaster_id"], "login": ch["broadcaster_login"], "name": ch["broadcaster_name"]}
        for ch in moderated.get("data", [])
    ]

    moderated_channels.append({"id": user["id"], "login": user["login"], "name": user["display_name"]})

    session["twitch_user"] = user
    session["twitch_token"] = token
    session["twitch_moderated_channels"] = moderated_channels

    if not await is_user_in(user["id"]) and token is not None:
        await add_user(user["login"], user["id"], token, user_response.get("refresh_token"))
    else:
        assert token is not None
        await save_twitch_token(user["login"], user["id"], token, user_response.get("refresh_token"))
    return redirect(url_for("twitch.bot_select"))


@twitch_bp.route("/logout")
async def logout():
    # Revoke the token with twitch
    token = session.get("twitch_token")
    if token:
        requests.post(
            "https://id.twitch.tv/oauth2/revoke",
            params={
                "client_id": TWITCH_CLIENT_ID,
                "token": token,
            },
        )
    # clear twitch-related keys
    session.pop("twitch_user", None)
    session.pop("twitch_token", None)
    session.pop("twitch_oauth_state", None)
    print("Removed twitch related settings")
    return redirect(url_for("index"))


@twitch_bp.route("/bot-select")
async def bot_select():
    if "twitch_user" not in session:
        return redirect(url_for("login"))
    user = session["twitch_user"]
    profile_image = user["profile_image_url"]

    bots_set: set[tuple[int, str]] = set()  # to ensure no copies
    for channel in session["twitch_moderated_channels"]:
        results = await get_bots(channel["login"])
        for result in results:
            bots_set.add(result)

    bots = list(bots_set)  # to ensure we can still index

    if len(bots) == 1:
        bot = bots[0]  # bot[1] == bot.id, bot[0] == bot.name
        return redirect(url_for("twitch.dashboard", bot_name=bot[0]))

    return render_template("twitch_select_bot.html", user=user, profile_image=profile_image, bots=bots)


@twitch_bp.route("/dashboard/<bot_name>")
async def dashboard(bot_name):
    if "twitch_user" not in session:
        return redirect(url_for("login"))
    user = session["twitch_user"]
    profile_image = user["profile_image_url"]
    bot_channels = await get_bot_twitch_channels(bot_name)

    return render_template(
        "twitch_dashboard.html", user=user, profile_image=profile_image, bot_channels=bot_channels, bot_name=bot_name
    )


@twitch_bp.route("/dashboard/<bot_name>/<channel_login>")
async def channel_dashboard(bot_name, channel_login):
    if "twitch_user" not in session:
        return redirect(url_for("login"))
    user = session["twitch_user"]
    profile_image = user["profile_image_url"]
    bot_id = await get_bot_id(bot_name)
    if not bot_id:
        return redirect(url_for("dashboard", bot_name=bot_name))
    command_details = await get_bot_commands(bot_id=bot_id, streamer_login=channel_login)

    return render_template(
        "twitch/commands_dashboard.html",
        channel_login=channel_login,
        command_details=command_details,
        profile_image=profile_image,
        bot_name=bot_name,
    )


@twitch_bp.route("/dashboard/<bot_name>/<channel_login>/add_command", methods=["GET", "POST"])
async def add_command(bot_name, channel_login):
    if "twitch_user" not in session:
        return redirect(url_for("login"))
    user = session["twitch_user"]
    if not await get_user(user["login"]):
        await add_user(
            username=user["login"], user_id=user["id"], access_token=session["twitch_token"], bot_id=1, refresh_token=None
        )
    profile_image = user["profile_image_url"]
    if request.method == "GET":
        return render_template(
            "dashboard/add_command_twitch.html", profile_image=profile_image, channel_login=channel_login, bot_name=bot_name
        )
    # POST SO PROCESS
    name = request.form.get("name", "")
    Reply = request.form.get("reply", "")
    user_lvl = request.form.get("user_lvl", "")
    bot_id = await get_bot_id(bot_name)
    assert bot_id is not None
    success = await add_bot_commands(name, Reply, user_lvl, channel_login, bot_id)
    if success:
        return redirect(url_for("channel_dashboard", channel_login=channel_login, bot_name=bot_name))
    else:
        return render_template("dashboard/add_command_twitch.html", profile_image=profile_image, bot_name=bot_name)


@twitch_bp.route("/dashboard/toggle-command/<command_id>/<bot_name>", methods=["POST"])
async def toggle_command(command_id, bot_name):
    if "twitch_user" not in session:
        return {"Error": "Unauthorized"}, 401

    success = await change_activity(command_id, bot_name)
    if success:
        return {"ok": True}, 200
    return {"error": "Failed to toggle"}, 500


@twitch_bp.route("/dashboard/delete-command/<command_id>/<bot_name>", methods=["POST"])
async def del_command(command_id, bot_name):
    if "twitch_user" not in session:
        return {"Error": "Unauthorized"}, 401

    success = await delete_command(command_id, bot_name)
    if success:
        return {"ok": True}, 200
    return {"error": "Failed to delete"}, 500


@twitch_bp.route("/twitch/dashboard/<bot_name>/<channel_login>/edit_command/<command_id>", methods=["GET", "POST"])
async def edit_command(bot_name, channel_login, command_id):
    if "twitch_user" not in session:
        return redirect(url_for("login"))
    user = session["twitch_user"]
    if not await get_user(user["login"]):
        await add_user(username=user["login"], user_id=user["id"], access_token=session["twitch_token"], refresh_token=None)
    profile_image = user["profile_image_url"]
    details = await get_specific_command(bot_name=bot_name, streamer_name=channel_login, command_id=command_id)
    if request.method == "GET":
        if details is None:
            return "Command not found", 400
        user_levels = ["Everyone", "Subscriber", "VIP", "Moderator", "Broadcaster"]
        return render_template(
            "dashboard/edit_command_twitch.html",
            command_details=details,
            profile_image=profile_image,
            user_lvls=user_levels,
            command_id=command_id,
            channel_login=channel_login,
            bot_name=bot_name,
        )

    # POST so process
    name = request.form.get("name", "")
    Reply = request.form.get("reply", "")
    user_lvl = request.form.get("user_lvl", "")
    active = "active" in request.form
    success = await edit_specific_command(bot_name, name, command_id, Reply, user_lvl, bool(active))
    if success:
        return redirect(url_for("channel_dashboard", channel_login=channel_login, bot_name=bot_name))
    else:
        user_levels = ["Everyone", "Subscriber", "VIP", "Moderator", "Broadcaster"]
        return render_template(
            "dashboard/edit_command_twitch.html",
            command_details=details,
            profile_image=profile_image,
            user_lvls=user_levels,
            command_id=command_id,
            channel_login=channel_login,
            bot_name=bot_name,
        )
