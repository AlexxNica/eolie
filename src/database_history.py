# Copyright (c) 2017 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from gi.repository import GLib, Gio

import sqlite3
import itertools
from time import time

from eolie.utils import noaccents, get_random_string
from eolie.localized import LocalizedCollation
from eolie.sqlcursor import SqlCursor


class DatabaseHistory:
    """
        Eolie history db
    """
    if GLib.getenv("XDG_DATA_HOME") is None:
        __LOCAL_PATH = GLib.get_home_dir() + "/.local/share/eolie"
    else:
        __LOCAL_PATH = GLib.getenv("XDG_DATA_HOME") + "/eolie"
    DB_PATH = "%s/history.db" % __LOCAL_PATH

    # SQLite documentation:
    # In SQLite, a column with type INTEGER PRIMARY KEY
    # is an alias for the ROWID.
    # Here, we define an id INT PRIMARY KEY but never feed it,
    # this make VACUUM not destroy rowids...
    __create_history = '''CREATE TABLE history (
                                               id INTEGER PRIMARY KEY,
                                               title TEXT NOT NULL,
                                               uri TEXT NOT NULL,
                                               guid TEXT NOT NULL,
                                               atime INT NOT NULL,
                                               mtime INT NOT NULL,
                                               popularity INT NOT NULL
                                               )'''

    def __init__(self):
        """
            Create database tables or manage update if needed
        """
        f = Gio.File.new_for_path(self.DB_PATH)
        if not f.query_exists():
            try:
                d = Gio.File.new_for_path(self.__LOCAL_PATH)
                if not d.query_exists():
                    d.make_directory_with_parents()
                # Create db schema
                with SqlCursor(self) as sql:
                    sql.execute(self.__create_history)
                    sql.commit()
            except Exception as e:
                print("DatabaseHistory::__init__(): %s" % e)

    def add(self, title, uri, guid=None, atime=None, mtime=None, commit=True):
        """
            Add a new entry to history, if exists, update it
            @param title as str
            @param uri as str
            @param atime as int
            @param mtime as int
            @param commit as bool
        """
        if not uri:
            return
        uri = uri.rstrip('/')
        if title is None:
            title = ""
        if atime is None:
            atime = int(time())
        if mtime is None:
            mtime = atime
        # Find an uniq guid
        while guid is None:
            guid = get_random_string(12)
            if self.exists_guid(guid):
                guid = None
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT popularity, atime FROM history\
                                  WHERE uri=?", (uri,))
            v = result.fetchone()
            if v is not None:
                # Never update history item with an older entry
                if v[1] > atime:
                    return
                sql.execute("UPDATE history set atime=?, mtime=?, title=?,\
                                 popularity=?, guid=?\
                             WHERE uri=?", (atime, mtime, title,
                                            v[0]+1, guid, uri))
            else:
                sql.execute("INSERT INTO history\
                                  (title, uri, atime, mtime, popularity, guid)\
                                  VALUES (?, ?, ?, ?, ?, ?)",
                            (title, uri, atime, mtime, 0, guid))
            if commit:
                sql.commit()

    def remove(self, history_id):
        """
            Remove item from history
            @param history id as int
        """
        with SqlCursor(self) as sql:
            sql.execute("DELETE from history\
                         WHERE rowid=?", (history_id,))
            sql.commit()

    def clear(self):
        """
            Clear history
        """
        with SqlCursor(self) as sql:
            sql.execute("DELETE from history")
            sql.commit()

    def get(self, atime):
        """
            Get history
            @param atime as int
            @return (str, str, int)
        """
        one_day = 86400
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT rowid, title, uri, atime\
                                  FROM history\
                                  WHERE atime >= ? AND atime <= ?\
                                  ORDER BY atime DESC LIMIT ?",
                                 (atime, atime + one_day, atime))
            return list(result)

    def get_id(self, title, uri):
        """
            Get history id
            @param title as str
            @param uri as str
            @return history_id as int
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT rowid\
                                  FROM history\
                                  WHERE title=? AND uri=?",
                                 (title, uri))
            v = result.fetchone()
            if v is not None:
                return v[0]
            return None

    def get_title(self, history_id):
        """
            Get history title
            @param history_id as int
            @return title as str
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT title\
                                  FROM history\
                                  WHERE rowid=?", (history_id,))
            v = result.fetchone()
            if v is not None:
                return v[0]
            return ""

    def get_uri(self, history_id):
        """
            Get history uri
            @param history_id as int
            @return uri as str
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT uri\
                                  FROM history\
                                  WHERE rowid=?", (history_id,))
            v = result.fetchone()
            if v is not None:
                return v[0]
            return ""

    def get_guid(self, history_id):
        """
            Get history item guid
            @param history_id as int
            @return guid as str
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT guid\
                                  FROM history\
                                  WHERE rowid=?", (history_id,))
            v = result.fetchone()
            if v is not None:
                return v[0]
            return ""

    def get_mtime(self, history_id):
        """
            Get history mtime
            @param history_id as int
            @return mtime as int
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT mtime\
                                  FROM history\
                                  WHERE rowid=?", (history_id,))
            v = result.fetchone()
            if v is not None:
                return v[0]
            return 0

    def get_atime(self, history_id):
        """
            Get history atime
            @param history_id as int
            @return atime as int
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT atime\
                                  FROM history\
                                  WHERE rowid=?", (history_id,))
            v = result.fetchone()
            if v is not None:
                return v[0]
            return 0

    def get_id_by_guid(self, guid):
        """
            Get id for guid
            @param guid as str
            @return id as int
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT rowid\
                                  FROM history\
                                  WHERE guid=?", (guid,))
            v = result.fetchone()
            if v is not None:
                return v[0]
            return None

    def get_ids_for_mtime(self, mtime):
        """
            Get ids that need to be synced related to mtime
            @param mtime as int
            @return [int]
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT rowid\
                                  FROM history\
                                  WHERE mtime > ?", (mtime,))
            return list(itertools.chain(*result))

    def set_title(self, history_id, title, commit=True):
        """
            Set history title
            @param history_id as int
            @param title as str
            @param commit as bool
        """
        with SqlCursor(self) as sql:
            sql.execute("UPDATE history\
                         SET title=?\
                         WHERE rowid=?", (title, history_id,))
            if commit:
                sql.commit()

    def set_atime(self, history_id, atime, commit=True):
        """
            Set history atime
            @param history_id as int
            @param atime as int
            @param commit as bool
        """
        with SqlCursor(self) as sql:
            sql.execute("UPDATE history\
                         SET atime=? where rowid=?", (atime, history_id))
            if commit:
                sql.commit()

    def set_mtime(self, history_id, mtime, commit=True):
        """
            Set history mtime
            @param history_id as int
            @param atime as int
            @param commit as bool
        """
        with SqlCursor(self) as sql:
            sql.execute("UPDATE history\
                         SET mtime=? where rowid=?", (mtime, history_id))
            if commit:
                sql.commit()

    def search(self, search, limit):
        """
            Search string in db (uri and title)
            @param search as str
            @param limit as int
            @return (str, str)
        """
        with SqlCursor(self) as sql:
            filter = '%' + search + '%'
            result = sql.execute("SELECT title, uri\
                                  FROM history\
                                  WHERE title LIKE ?\
                                   OR uri LIKE ?\
                                  ORDER BY popularity DESC,\
                                  atime DESC LIMIT ?",
                                 (filter, filter, limit))
            return list(result)

    def exists_guid(self, guid):
        """
            Check if guid exists in db
            @return bool
        """
        with SqlCursor(self) as sql:
            result = sql.execute("SELECT guid FROM history\
                                  WHERE guid=?", (guid,))
            v = result.fetchone()
            return v is not None

    def get_cursor(self):
        """
            Return a new sqlite cursor
        """
        try:
            c = sqlite3.connect(self.DB_PATH, 600.0)
            c.create_collation('LOCALIZED', LocalizedCollation())
            c.create_function("noaccents", 1, noaccents)
            return c
        except:
            exit(-1)

    def drop_db(self):
        """
            Drop database
        """
        try:
            f = Gio.File.new_for_path(self.DB_PATH)
            f.trash()
        except Exception as e:
            print("DatabaseHistory::drop_db():", e)

#######################
# PRIVATE             #
#######################
