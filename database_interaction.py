import sqlite3
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path


def get_requested_database(server_id: int, db_name: str) -> Path | None:
    server_dirs = {
        1273776575266951268: "Shark-Bot",
        1412851365910286379: "FSAIHelper",
        1066090135839580231: "Shark-Bot",
    }

    folder = server_dirs.get(int(server_id))
    if folder is None:
        return None

    home_path = Path(__file__).parent.parent / folder

    matches = list(home_path.rglob("*.db"))

    for match in matches:
        try:
            if db_name == str(match.relative_to(home_path / "databases")):
                return match
        except Exception:
            continue
    return None


def get_roles_database_info(server_id: int, db_name: str) -> dict[str, tuple[list[str], list[str]]] | None:
    db_path = get_requested_database(server_id, db_name)
    if not db_path:
        return None

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    table_to_column_and_type: dict[str, tuple[list[str], list[str]]] = {}  # table name -> (column, type)
    for table in tables:
        query = cur.execute(f"PRAGMA table_info({table})").fetchall()
        columns, types = [row[1] for row in query], [row[2] for row in query]
        table_to_column_and_type[table] = (columns, types)

    conn.close()

    return table_to_column_and_type


def get_react_roles_internal(server_id: int) -> dict[int, dict[str, dict[str, tuple[int, str, int]]]] | None:
    db_path = get_requested_database(server_id, "roles.db")
    if not db_path:
        return None

    db_info = get_roles_database_info(server_id, "roles.db")
    assert db_info is not None
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    Emoji_Result = namedtuple(
        "EmojiResult",
        ("roleName", "roleId", "guildName", "guildId", "roleSetName", "emojiName", "emojiIsAnimated", "discordEmojiId"),
    )

    SQL_QUERY = r"""SELECT
        r.role_id,
        r.name as roleName,
        g.name as guildName,
        g.guild_id as guildId,
        rs.name as roleSetName,
        e.name as emojiName,
        e.animated as emojiIsAnimated,
        e.discord_id as discordEmojiId
    FROM
        roles as r
    INNER JOIN
        roleSets AS rs
        ON r.roleSet_ID = rs.id
    INNER JOIN
        guilds as g
        ON r.guild_id = g.id
    INNER JOIN
        emojis AS e
        ON r.emoji_id = e.id
    """

    emojiResults: list[Emoji_Result] = []

    emojiMap: dict[
        int, dict[str, dict[str, tuple[int, str, int]]]
    ] = {}  # guild_id -> roleSetName -> emoji name or link -> (emoji_id, role name)
    cur.row_factory = sqlite3.Row
    results = cur.execute(SQL_QUERY).fetchall()
    for result in results:
        emojiResults.append(
            Emoji_Result(
                roleName=result["roleName"],
                roleId=result["role_id"],
                guildName=result["guildName"],
                roleSetName=result["roleSetName"],
                emojiName=result["emojiName"],
                emojiIsAnimated=result["emojiIsAnimated"],
                discordEmojiId=result["discordEmojiId"],
                guildId=result["guildId"],
            )
        )

    for r in emojiResults:
        if r.guildId != server_id:
            continue

        if r.guildId not in emojiMap.keys():
            emojiMap[r.guildId] = {}
        if r.roleSetName not in emojiMap[r.guildId].keys():
            emojiMap[r.guildId][r.roleSetName] = {}
        if r.discordEmojiId:
            emojiMap[r.guildId][r.roleSetName][r.emojiName] = (r.discordEmojiId, r.roleName, r.roleId)
        else:
            emojiMap[r.guildId][r.roleSetName][r.emojiName] = (r.discordEmojiId, r.roleName, r.roleId)
    conn.close()
    return emojiMap


def put_emoji_in_table(db_path: Path, animated: bool, emoji_name: str, discord_id: int | None) -> int:
    """
    Puts emoji in the SQL table and returns the emoji ID
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO emojis (animated, name, discord_id) VALUES (?, ?, ?)",
        (animated, emoji_name.replace("\ufe0f", "").replace("\ufe0e", ""), discord_id),
    )
    conn.commit()
    return cur.execute("SELECT id FROM emojis WHERE name=?", (emoji_name,)).fetchone()[0]


def put_guild_in_table(db_path: Path, guild_name: str, guild_id: int | None = None) -> int:
    """
    Puts Guild in the SQL table and returns the guild ID
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if guild_name == "⊹˚₊⟡₊‧⁺˖ The Cult of Shark ˖⁺‧₊⟡₊˚⊹":
        guild_name = "shark squad"

    if guild_id is not None:
        cur.execute("INSERT OR IGNORE INTO guilds (name, guild_id) VALUES (?, ?)", (guild_name, guild_id))
        conn.commit()
    to_return = cur.execute("SELECT id FROM guilds WHERE name = ?", (guild_name,)).fetchone()
    if to_return is not None:
        return to_return[0]
    raise ValueError("Value is None")


def put_role_set_in_table(db_path: Path, role_set_name: str, guild_table_id: int) -> int:
    """
    Puts role set in the SQL table and returns the role set ID
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO roleSets (name, guild_table_id, message_id) VALUES (?, ?, ?)", (role_set_name, guild_table_id, 0)
    )
    conn.commit()
    return cur.execute("SELECT id FROM roleSets WHERE name = ?", (role_set_name,)).fetchone()[0]


def put_role_in_table(
    db_path: Path, role_name: str, role_id: int, emoji_table_id: int, guild_table_id: int, role_set_table_id: int
):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO roles (name, role_id, emoji_id, guild_id, roleSet_ID) VALUES (?, ?, ?, ?, ?)",
        (role_name, role_id, emoji_table_id, guild_table_id, role_set_table_id),
    )
    conn.commit()
    conn.close()


def add_role(
    role_name: str,
    role_id: int,
    role_emoji_name: str,
    is_emoji_animated: bool,
    role_emoji_id: int | None,
    role_set_name: str,
    guild_name: str,
    guild_id: int,
) -> bool:
    db_path = get_requested_database(guild_id, "roles.db")
    if not db_path:
        return False
    try:
        emoji_id = put_emoji_in_table(
            db_path=db_path, animated=is_emoji_animated, emoji_name=role_emoji_name, discord_id=role_emoji_id
        )
        guild_table_id = put_guild_in_table(db_path=db_path, guild_name=guild_name, guild_id=guild_id)
        roleSet_id = put_role_set_in_table(db_path, role_set_name, guild_table_id)
        put_role_in_table(db_path, role_name, role_id, emoji_id, guild_table_id, roleSet_id)
    except sqlite3.OperationalError as e:
        raise e
    return True


# print(get_react_roles_internal(1273776575266951268))


@dataclass()
class CustomCommand:
    id: int | None
    name: str
    reply: str
    aliases: list[str] | None
    mod_only: bool
    active: bool


# custom commands
def get_custom_commands(guild_id: int):
    db_path = get_requested_database(guild_id, "commands.db")

    if not db_path:
        raise ValueError("Database Path does not exist.")

    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """
            SELECT c.id, c.name, c.reply, c.mod_only, GROUP_CONCAT(ca.alias, '|'), c.active
            FROM commands as c
            JOIN command_aliases as ca ON c.id = ca.command_id
            GROUP BY c.id
        """
    )
    results = cur.fetchall()
    commands: list[CustomCommand] = []
    for result in results:
        commands.append(
            CustomCommand(
                id=result[0],
                name=result[1],
                reply=result[2],
                mod_only=bool(result[3]),
                aliases=result[4].split("|"),
                active=bool(result[5]),
            )
        )

    cur = conn.execute(
        "SELECT id, name, reply, mod_only, active FROM commands WHERE id NOT IN (select command_id FROM command_aliases)"
    )
    results = cur.fetchall()
    for result in results:
        commands.append(
            CustomCommand(
                id=result[0].replace("!", ""),
                name=result[1],
                reply=result[2],
                mod_only=bool(result[3]),
                aliases=None,
                active=bool(result[4]),
            )
        )

    conn.close()

    return commands


def add_command(guild_id: int, command: CustomCommand):
    db_path = get_requested_database(guild_id, "commands.db")

    if not db_path:
        raise ValueError("Database not found")

    conn = sqlite3.connect(db_path)
    command.name = "!" + command.name
    conn.execute(
        "INSERT OR IGNORE INTO commands (name, reply, mod_only, active) VALUES (?, ?, ?, ?)",
        (command.name, command.reply, command.mod_only, command.active),
    )

    conn.commit()

    if command.aliases is None:
        return

    cur = conn.execute("SELECT id FROM commands WHERE name=?", (command.name,))
    id = cur.fetchone()
    if id is None:
        raise RuntimeError("Command was not added.")
    command.id = id
    for alias in command.aliases:
        conn.execute(
            "INSERT OR IGNORE INTO command_aliases (alias, command_id) VALUES (?, ?)",
            (alias, command.id),
        )
    conn.commit()
    conn.close()


def edit_command(guild_id: int, command: CustomCommand):
    db_path = get_requested_database(guild_id, "commands.db")

    if not db_path:
        raise ValueError("Database not found")

    conn = sqlite3.connect(db_path)
    command.name = "!" + command.name
    conn.execute(
        "UPDATE commands SET active=?, name=?, reply=?, mod_only=? WHERE id=?",
        (command.active, command.name, command.reply, command.mod_only, command.id),
    )
    conn.commit()
    conn.close()


def delete_command(guild_id: int, command: CustomCommand):
    db_path = get_requested_database(guild_id, "commands.db")

    if not db_path:
        raise ValueError("Database not found")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.commit()
    conn.execute("DELETE FROM commands WHERE id=?", (command.id,))
    conn.commit()
    conn.close()
