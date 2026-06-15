import asyncio
from pathlib import Path

import aiosqlite

db_path = Path("databases/Twitch_bots.db")


async def init_db():
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS twitch_bots
                (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE,
                    relative_path TEXT UNIQUE NOT NULL
                )""")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS twitch_channels (
                id INTEGER PRIMARY KEY,
                channel_login TEXT UNIQUE NOT NULL,
                bot_id INTEGER NOT NULL REFERENCES twitch_bots(id) ON DELETE CASCADE
            )
            """
        )

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS twitch_users
                (
                    twitch_id TEXT NOT NULL,
                    twitch_login TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    channel_id INTEGER REFERENCES twitch_channels(id),
                    linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    token_refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(channel_id, twitch_id)
                )
        """)

        home_path = Path(__file__).parent.parent

        await conn.execute(
            "INSERT OR IGNORE INTO twitch_bots (name, relative_path) VALUES (?, ?)",
            ("shark-bot", str(home_path / "Sharkxxbot" / "databases" / "chat_commands.db")),
        )

        await conn.execute(
            "INSERT OR IGNORE INTO twitch_channels (channel_login, bot_id) VALUES (?, (SELECT id FROM twitch_bots WHERE name=?))",
            ("sharkocalypse", "shark-bot"),
        )
        await conn.execute(
            "INSERT OR IGNORE INTO twitch_channels (channel_login, bot_id) VALUES (?, (SELECT id FROM twitch_bots WHERE name=?))",
            ("spiderbyte2007", "shark-bot"),
        )

        await conn.execute(
            "INSERT OR IGNORE INTO twitch_users (twitch_id, twitch_login, access_token, refresh_token, "
            "channel_id) values (?, ?, ?, ?, (SELECT id FROM twitch_channels WHERE channel_login=?))",
            ("467322152", "spiderbyte2007", "placeholder", "placeholder", "spiderbyte2007"),
        )

        print("Done with all tables")
        await conn.commit()


asyncio.run(init_db())


async def get_bot_path(bot_name: str = "", bot_id: int = 0):
    async with aiosqlite.connect(db_path) as conn:
        if bot_id == 0:
            async with conn.execute("SELECT name FROM twitch_bots") as cur:
                results = await cur.fetchall()
                if results is None:
                    return None
                for result in results:
                    if result[0].lower() == bot_name.lower():
                        async with conn.execute("SELECT relative_path FROM twitch_bots WHERE name=?", (bot_name,)) as cur:
                            path = await cur.fetchone()
                            assert path
                            path = path[0]
                            return Path(path)
        else:
            async with conn.execute("SELECT relative_path FROM twitch_bots WHERE id=?", (bot_id,)) as cur:
                result = await cur.fetchone()
                if result is None:
                    return None
                path = result[0]
                return Path(path)


async def get_bot_id(bot_name: str) -> int | None:
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT id FROM twitch_bots WHERE name=?", (bot_name,)) as cur:
            result = await cur.fetchone()
            if result is None:
                return None
            try:
                return int(result[0])
            except Exception:
                return 0


async def get_bot_commands(streamer_login: str, bot_name: str = "", bot_id: int = 0):
    "Returns the command names, replies and user level for the commands"
    path = await get_bot_path(bot_name, bot_id)
    if path is None:
        return None
    async with aiosqlite.connect(path) as conn:
        async with conn.execute(
            "SELECT id, name, reply, user_level, active FROM commands WHERE streamer=?", (streamer_login,)
        ) as cur:
            results = await cur.fetchall()
            if results is None:
                return None
            command_ids: list[int] = []
            command_names: list[str] = []
            command_reply: list[str] = []
            command_user_level: list[str] = []
            command_activity: list[bool] = []
            for result in results:
                command_ids.append(result[0])
                command_names.append(result[1])
                command_reply.append(result[2])
                command_user_level.append(result[3])
                command_activity.append(bool(result[4]))

            return list(zip(command_ids, command_names, command_reply, command_user_level, command_activity))


async def add_bot_commands(
    command_name: str, command_reply: str, command_user_level: str, streamer_login: str, bot_id: int = 0, bot_name: str = ""
):
    path = await get_bot_path(bot_name, bot_id)
    if path is None:
        print("path is none")
        return False
    async with aiosqlite.connect(path) as conn:
        name = f"!{command_name}"
        await conn.execute(
            "INSERT OR IGNORE INTO commands (name, reply, user_level, active, streamer) VALUES (?, ?, ?, ?, ?)",
            (name, command_reply, command_user_level, False, streamer_login),
        )
        await conn.commit()
        return True


async def get_command_id(command_name: str, streamer_name: str):
    if command_name == "":
        return None
    path = await get_bot_path(bot_id=1)
    if path is None:
        return None
    async with aiosqlite.connect(path) as conn:
        async with conn.execute("SELECT id FROM commands WHERE name=? AND streamer=?", (command_name, streamer_name)) as cur:
            result = await cur.fetchone()
            if result is None:
                return None
            id = result[0]
            return id


async def get_specific_command(streamer_name: str, command_name: str = "", command_id: int = 0):
    if command_name == "" and command_id == 0:
        return None
    path = await get_bot_path(bot_id=1)
    if path is None:
        print("Path is none")
        return None
    async with aiosqlite.connect(path) as conn:
        if command_id == 0:
            name = f"!{command_name}"
            id = await get_command_id(streamer_name, name)
            async with conn.execute("SELECT reply, user_level, active FROM commands WHERE name=?", (name,)) as cur:
                result = await cur.fetchone()
                if result is None:
                    return None
                reply = result[0]
                user_level = result[1]
                active = bool(result[2])
                return command_name, reply, user_level, id, active
        else:
            async with conn.execute("SELECT name, reply, user_level, active FROM commands WHERE id=?", (command_id,)) as cur:
                result = await cur.fetchone()
                if result is None:
                    return None
                name = result[0]
                reply = result[1]
                user_level = result[2]
                active = bool(result[3])
                return name, reply, user_level, command_id, active


async def edit_specific_command(command_name: str, command_id: int, command_reply: str, user_level: str, activity: bool):
    path = await get_bot_path(bot_id=1)
    if path is None:
        return None
    async with aiosqlite.connect(path) as conn:
        name = f"!{command_name}"
        await conn.execute(
            "UPDATE commands SET name=?, reply=?, user_level=?, active=? WHERE id=?",
            (name, command_reply, user_level, activity, command_id),
        )
        await conn.commit()
        return True


async def change_activity(command_id: int):
    path = await get_bot_path(bot_id=1)
    if path is None:
        return None
    async with aiosqlite.connect(path) as conn:
        await conn.execute("UPDATE commands SET active= NOT active WHERE id=?", (command_id,))
        await conn.commit()
        return True


async def delete_command(command_id: int):
    path = await get_bot_path(bot_id=1)
    if path is None:
        return
    async with aiosqlite.connect(path) as conn:
        await conn.execute("DELETE FROM commands WHERE id=?", (command_id,))
        await conn.commit()
        return True


async def get_bot_twitch_channels(bot_name: str) -> list[str]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
                SELECT tc.channel_login
                FROM twitch_channels as tc
                JOIN twitch_bots as tb ON tc.bot_id = tb.id
                WHERE tb.name=?
            """,
            (bot_name,),
        ) as cur:
            rows = await cur.fetchall()
    return [row["channel_login"] for row in rows]


async def get_token_for_channel(channel_login: str) -> dict | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
            SELECT tu.*
            FROM twitch_users as tu
            JOIN twitch_channels as tc ON tu.channel_id = tc.id
            WHERE tc.channel_login = ?
            """,
            (channel_login,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def add_user(
    username: str, user_id: str, access_token: str, refresh_token: str, bot_id: int = 0, bot_name: str = ""
) -> bool | None:

    if bot_id == 0:
        botId = await get_bot_id(bot_name)
        if botId is None:
            return None
        bot_id = botId

    async with aiosqlite.connect(db_path) as conn:
        if bot_id == 0 and bot_name == "":
            await conn.execute(
                "INSERT OR REPLACE INTO twitch_users (twitch_id, twitch_login, access_token, refresh_token) VALUES "
                "(?, ?, ?, ?)",
                (user_id, username, access_token, refresh_token),
            )
            await conn.commit()
            return True

        await conn.execute(
            "INSERT OR REPLACE INTO twitch_users (twitch_id, twitch_login, access_token, refresh_token, bot_id) VALUES "
            "(?, ?, ?, ?, ?)",
            (user_id, username, access_token, refresh_token, bot_id),
        )
        await conn.commit()
        return True


async def is_user_in(user_id: str) -> bool:
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT COUNT(*) FROM twitch_users WHERE twitch_id=?", (user_id,)) as cur:
            result = await cur.fetchone()
            if result is None or result[0] == 0:
                return False
            return True


async def get_user(login: str):
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        raw_row = await conn.execute("SELECT * FROM twitch_users WHERE twitch_login=?", (login,))
        row = await raw_row.fetchone()
        return dict(row) if row else None


async def save_twitch_token(login: str, user_id: str, access_token: str, refresh_token: str):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO twitch_users (twitch_login, twitch_id, access_token, refresh_token)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (twitch_login) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_refreshed_at = CURRENT_TIMESTAMP
            """,
            (login, user_id, access_token, refresh_token),
        )
        await conn.commit()
        await conn.close()


# print(get_bot_commands("shark-bot"))
