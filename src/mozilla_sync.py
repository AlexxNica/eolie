# Copyright (c) 2017 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
# Fork of https://github.com/mozilla-services/syncclient
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

from gi.repository import Gio, Secret

from pickle import dump, load
from hashlib import sha256
from binascii import hexlify
import json
import six
import hmac
import base64
import math
import requests
from time import time
from Crypto.Cipher import AES
from Crypto import Random
from requests_hawk import HawkAuth
from fxa.core import Client as FxAClient, Session as FxASession
from fxa.crypto import quick_stretch_password
from threading import Thread

from eolie.define import El
from eolie.utils import debug
from eolie.sqlcursor import SqlCursor


TOKENSERVER_URL = "https://token.services.mozilla.com/"
FXA_SERVER_URL = "https://api.accounts.firefox.com"


class SyncWorker:
    """
       Manage sync with mozilla server, will start syncing on init
    """

    def __init__(self):
        """
            Init worker
        """
        self.__stop = True
        self.__mtimes = {"bookmarks": 0.1, "history": 0.1}
        self.__status = False
        self.__client = MozillaSync()
        self.__session = None

    def sync(self, first_sync=False):
        """
            Start syncing, you need to check sync_status property
            @param first_sync as bool
        """
        if self.syncing:
            return True
        self.__username = ""
        self.__password = ""
        self.__stop = False
        Secret.Service.get(Secret.ServiceFlags.NONE, None,
                           self.__on_get_secret, first_sync, False)
        return True

    def push_history(self, history_id):
        """
            Add history id to remote history
            A first call to sync() is needed to populate secrets
            @param history_id as int
        """
        if Gio.NetworkMonitor.get_default().get_network_available():
            thread = Thread(target=self.__push_history, args=(history_id,))
            thread.daemon = True
            thread.start()

    def remove_from_history(self, guid):
        """
            Remove history id from remote history
            A first call to sync() is needed to populate secrets
            @param guid as str
        """
        if Gio.NetworkMonitor.get_default().get_network_available():
            thread = Thread(target=self.__remove_from_history, args=(guid,))
            thread.daemon = True
            thread.start()

    def delete_secret(self):
        """
            Delete sync secret
        """
        self.__username = ""
        self.__password = ""
        Secret.Service.get(Secret.ServiceFlags.NONE, None,
                           self.__on_get_secret, False, True)

    def stop(self):
        """
            Stop update
        """
        self.__stop = True

    @property
    def mtimes(self):
        """
            Sync engine modification times
            @return {}
        """
        return self.__mtimes

    @property
    def syncing(self):
        """
            True if sync is running
            @return bool
        """
        return not self.__stop

    @property
    def status(self):
        """
            True if sync is working
            @return bool
        """
        return self.__status

    @property
    def username(self):
        """
            Get username
            @return str
        """
        return self.__username

#######################
# PRIVATE             #
#######################
    def __get_session_bulk_keys(self):
        """
            Get a session decrypt keys
            @return keys as (b"", b"")
        """
        if self.__session is None:
            self.__session = FxASession(self.__client.client,
                                        self.__username,
                                        quick_stretch_password(
                                                        self.__username,
                                                        self.__password),
                                        self.__uid,
                                        self.__token)
            self.__session.keys = [b"", self.__keyB]
        try:
            self.__status = True
            self.__session.check_session_status()
            bid_assertion, key = self.__client.get_browserid_assertion(
                                                            self.__session)
            bulk_keys = self.__client.connect(bid_assertion, key)
        except Exception as e:
            self.__status = False
            raise e
        return bulk_keys

    def __push_history(self, history_id):
        """
            Push history
            @param history_id as int
        """
        if not self.__username or not self.__password:
            self.__stop = True
            return
        try:
            bulk_keys = self.__get_session_bulk_keys()
            record = {}
            record["histUri"] = El().history.get_uri(history_id)
            record["id"] = El().history.get_guid(history_id)
            record["title"] = El().history.get_title(history_id)
            atime = 1000000 * El().history.get_atime(history_id)
            record["visits"] = [{"date": atime, "type": 1}]
            debug("pushing %s" % record)
            self.__client.add_history(record, bulk_keys)
        except Exception as e:
            print("SyncWorker::__push_history():", e)
        self.__stop = True

    def __remove_from_history(self, guid):
        """
            Remove from history
            @param guid as str
        """
        if not self.__username or not self.__password:
            self.__stop = True
            return
        try:
            debug("deleting %s" % guid)
            self.__client.client.delete_record("history", guid)
        except Exception as e:
            print("SyncWorker::__remove_from_history():", e)
        self.__stop = True

    def __sync(self, first_sync):
        """
            Sync Eolie objects (bookmarks, history, ...) with Mozilla Sync
            @param first_sync as bool
        """
        debug("Start syncing")
        if not self.__username or not self.__password:
            self.__stop = True
            return
        try:
            self.__mtimes = load(open(El().LOCAL_PATH + "/mozilla_sync.bin",
                                 "rb"))
        except:
            self.__mtimes = {"bookmarks": 0.1, "history": 0.1}
        try:
            bulk_keys = self.__get_session_bulk_keys()
            new_mtimes = self.__client.client.info_collections()

            ######################
            # History Management #
            ######################
            debug("local history: %s, remote history: %s" % (
                                                 self.__mtimes["history"],
                                                 new_mtimes["history"]))
            # Only pull if something new available
            if self.__mtimes["history"] != new_mtimes["history"]:
                self.__pull_history(bulk_keys)
            ########################
            # Bookmarks Management #
            ########################
            debug("local bookmarks: %s, remote bookmarks: %s" % (
                                                 self.__mtimes["bookmarks"],
                                                 new_mtimes["bookmarks"]))
            # Push new bookmarks
            self.__push_bookmarks(bulk_keys)
            # Only pull if something new available
            if self.__mtimes["bookmarks"] != new_mtimes["bookmarks"]:
                self.__pull_bookmarks(bulk_keys, first_sync)
            # Update last sync mtime
            self.__mtimes = self.__client.client.info_collections()
            dump(self.__mtimes,
                 open(El().LOCAL_PATH + "/mozilla_sync.bin", "wb"))
            debug("Stop syncing")
        except Exception as e:
            print("SyncWorker::__sync():", e)
        self.__stop = True

    def __push_bookmarks(self, bulk_keys):
        """
            Push to bookmarks
            @param bulk keys as KeyBundle
            @param start time as float
            @raise StopIteration
        """
        debug("push bookmarks")
        parents = []
        for bookmark_id in El().bookmarks.get_ids_for_mtime(
                                                   self.__mtimes["bookmarks"]):
            parent_guid = El().bookmarks.get_parent_guid(bookmark_id)
            # No parent, move it to unfiled
            if parent_guid is None:
                parent_guid = "unfiled"
            parent_id = El().bookmarks.get_id_by_guid(parent_guid)
            if parent_id not in parents:
                parents.append(parent_id)
            record = {}
            record["bmkUri"] = El().bookmarks.get_uri(bookmark_id)
            record["id"] = El().bookmarks.get_guid(bookmark_id)
            record["title"] = El().bookmarks.get_title(bookmark_id)
            record["tags"] = El().bookmarks.get_tags(bookmark_id)
            record["parentid"] = El().bookmarks.get_parent_guid(bookmark_id)
            record["type"] = "bookmark"
            debug("pushing %s" % record)
            self.__client.add_bookmark(record, bulk_keys)
        # Del old bookmarks
        for bookmark_id in El().bookmarks.get_deleted_ids():
            parent_guid = El().bookmarks.get_parent_guid(bookmark_id)
            # No parent, move it to unfiled
            if parent_guid is None:
                parent_guid = "unfiled"
            parent_id = El().bookmarks.get_id_by_guid(parent_guid)
            if parent_id not in parents:
                parents.append(parent_id)
            guid = El().bookmarks.get_guid(bookmark_id)
            debug("deleting %s" % guid)
            self.__client.client.delete_record("bookmarks", guid)
            El().bookmarks.remove(bookmark_id)
        # Push parents in this order, parents near root are handle later
        # As otherwise, order will be broken by new children updates
        while parents:
            parent_id = parents.pop(0)
            parent_guid = El().bookmarks.get_guid(parent_id)
            parent_name = El().bookmarks.get_title(parent_id)
            children = El().bookmarks.get_children(parent_guid)
            # So search if children in parents
            found = False
            for child_guid in children:
                child_id = El().bookmarks.get_id_by_guid(child_guid)
                if child_id in parents:
                    found = True
                    break
            # Handle children first
            if found:
                parents.append(parent_id)
                debug("later: %s" % parent_name)
                continue
            record = {}
            record["id"] = parent_guid
            record["type"] = "folder"
            record["parentid"] = El().bookmarks.get_parent_guid(parent_id)
            record["parentName"] = El().bookmarks.get_parent_name(parent_id)
            record["title"] = parent_name
            record["children"] = children
            debug("pushing parent %s" % record)
            self.__client.add_bookmark(record, bulk_keys)
        El().bookmarks.clean_tags()

    def __pull_bookmarks(self, bulk_keys, first_sync):
        """
            Pull from bookmarks
            @param bulk_keys as KeyBundle
            @param first_sync as bool
            @raise StopIteration
        """
        debug("pull bookmarks")
        SqlCursor.add(El().bookmarks)
        records = self.__client.get_bookmarks(bulk_keys)
        # We get all guids here and remove them while sync
        # At the end, we have deleted records
        # On fist sync, keep all
        if first_sync:
            to_delete = []
        else:
            to_delete = El().bookmarks.get_guids()
        for record in records:
            bookmark = record["payload"]
            if "type" not in bookmark.keys() or\
                    bookmark["type"] not in ["folder", "bookmark"]:
                continue
            bookmark_id = El().bookmarks.get_id_by_guid(bookmark["id"])
            # This bookmark exists, remove from to delete
            if bookmark["id"] in to_delete:
                to_delete.remove(bookmark["id"])
            # Nothing to apply, continue
            if El().bookmarks.get_mtime(bookmark_id) >= record["modified"]:
                continue
            debug("pulling %s" % record)
            if bookmark_id is None:
                if "bmkUri" in bookmark.keys():
                    # Use parent name if no bookmarks tags
                    if "tags" not in bookmark.keys() or\
                            not bookmark["tags"]:
                        if "parentName" in bookmark.keys() and\
                                bookmark["parentName"]:
                            bookmark["tags"] = [bookmark["parentName"]]
                        else:
                            bookmark["tags"] = []
                    bookmark_id = El().bookmarks.add(bookmark["title"],
                                                     bookmark["bmkUri"],
                                                     bookmark["id"],
                                                     bookmark["tags"],
                                                     False)
                else:
                    bookmark["tags"] = []
                    bookmark_id = El().bookmarks.add(bookmark["title"],
                                                     bookmark["id"],
                                                     bookmark["id"],
                                                     bookmark["tags"],
                                                     False)
            else:
                El().bookmarks.set_title(bookmark_id,
                                         bookmark["title"],
                                         False)
                if "bmkUri" in bookmark.keys():
                    El().bookmarks.set_uri(bookmark_id,
                                           bookmark["bmkUri"],
                                           False)
                elif "children" in bookmark.keys():
                    position = 0
                    for child in bookmark["children"]:
                        bid = El().bookmarks.get_id_by_guid(child)
                        El().bookmarks.set_position(bid,
                                                    position,
                                                    False)
                        position += 1
                # Remove previous tags
                current_tags = El().bookmarks.get_tags(bookmark_id)
                for tag in El().bookmarks.get_tags(bookmark_id):
                    if "tags" in bookmark.keys() and\
                            tag not in bookmark["tags"]:
                        tag_id = El().bookmarks.get_tag_id(tag)
                        current_tags.remove(tag)
                        El().bookmarks.del_tag_from(tag_id,
                                                    bookmark_id,
                                                    False)
                if "tags" in bookmark.keys():
                    for tag in bookmark["tags"]:
                        # Tag already associated
                        if tag in current_tags:
                            continue
                        tag_id = El().bookmarks.get_tag_id(tag)
                        if tag_id is None:
                            tag_id = El().bookmarks.add_tag(tag, False)
                        El().bookmarks.add_tag_to(tag_id, bookmark_id, False)
            El().bookmarks.set_mtime(bookmark_id,
                                     record["modified"],
                                     False)
            if "parentName" in bookmark.keys():
                El().bookmarks.set_parent(bookmark_id,
                                          bookmark["parentid"],
                                          bookmark["parentName"],
                                          False)
        for guid in to_delete:
            debug("deleting: %s" % guid)
            bookmark_id = El().bookmarks.get_id_by_guid(guid)
            if bookmark_id is not None:
                El().bookmarks.remove(bookmark_id, False)
        El().bookmarks.clean_tags()  # Will commit
        SqlCursor.remove(El().bookmarks)

    def __pull_history(self, bulk_keys):
        """
            Pull from history
            @param bulk_keys as KeyBundle
            @raise StopIteration
        """
        debug("pull history")
        SqlCursor.add(El().history)
        records = self.__client.get_history(bulk_keys)
        for record in records:
            history = record["payload"]
            if "histUri" not in history.keys():
                continue
            history_id = El().history.get_id_by_guid(history["id"])
            # Nothing to apply, continue
            if El().history.get_mtime(history_id) >= record["modified"]:
                continue
            # Try to get visit date
            try:
                atime = round(int(history["visits"][0]["date"]) / 1000000, 2)
            except:
                continue
            # History item had been sync last sync
            if atime < self.__mtimes["history"]:
                continue
            # Ignore page with no title
            if not history["title"]:
                continue
            debug("pulling %s" % record)
            title = history["title"].rstrip().lstrip()
            if history_id is None:
                history_id = El().history.add(title,
                                              history["histUri"],
                                              history["id"],
                                              atime,
                                              record["modified"],
                                              False)
            else:
                El().history.set_title(history_id,
                                       title,
                                       False)
                El().history.set_atime(history_id,
                                       atime,
                                       False)
                El().history.set_mtime(history_id,
                                       record["modified"],
                                       False)
        with SqlCursor(El().history) as sql:
            sql.commit()
        SqlCursor.remove(El().history)

    def __on_get_secret(self, source, result, first_sync, delete):
        """
            Store secret proxy
            @param source as GObject.Object
            @param result as Gio.AsyncResult
            @param fisrt_sync as bool
            @param delete as bool
        """
        try:
            secret = Secret.Service.get_finish(result)
            SecretSchema = {
                "sync": Secret.SchemaAttributeType.STRING
            }
            SecretAttributes = {
                "sync": "mozilla",
            }
            schema = Secret.Schema.new("org.gnome.Eolie",
                                       Secret.SchemaFlags.NONE,
                                       SecretSchema)
            secret.search(schema, SecretAttributes, Secret.ServiceFlags.NONE,
                          None, self.__on_secret_search, first_sync, delete)
        except Exception as e:
            print("SyncWorker::__on_get_secret()", e)

    def __on_load_secret(self, source, result, first_sync):
        """
            Set params and start sync
            @param source as GObject.Object
            @param result as Gio.AsyncResult
            @param first_sync as bool
        """
        try:
            secret = source.get_secret()
            attributes = source.get_attributes()
            if secret is not None:
                self.__username = attributes["login"]
                self.__password = secret.get().decode('utf-8')
                self.__token = attributes["token"]
                self.__uid = attributes["uid"]
                self.__keyB = base64.b64decode(attributes["keyB"])
                if Gio.NetworkMonitor.get_default().get_network_available():
                    thread = Thread(target=self.__sync,
                                    args=(first_sync,))
                    thread.daemon = True
                    thread.start()
        except Exception as e:
            print("SyncWorker::__on_load_secret()", e)

    def __on_secret_search(self, source, result, first_sync, delete):
        """
            Set username/password input
            @param source as GObject.Object
            @param result as Gio.AsyncResult
            @param first_sync as bool
            @param delete as bool
        """
        try:
            if result is not None:
                items = source.search_finish(result)
                if not items:
                    return
                if delete:
                    items[0].delete(None, None)
                else:
                    items[0].load_secret(None,
                                         self.__on_load_secret,
                                         first_sync)
            else:
                # Sync not configured, just remove pending deleted bookmarks
                for bookmark_id in El().get_deleted_ids():
                    El().bookmarks.remove(bookmark_id)
        except Exception as e:
            print("SyncWorker::__on_secret_search()", e)


class MozillaSync(object):
    """
        Sync client
    """
    def __init__(self):
        """
            Init client
        """
        self.__client = FxAClient()

    def login(self, login, password):
        """
            Login to FxA and get the keys.
            @param login as str
            @param password as str
            @return fxaSession
        """
        fxaSession = self.__client.login(login, password, keys=True)
        fxaSession.fetch_keys()
        return fxaSession

    def connect(self, bid_assertion, key):
        """
            Connect to sync using FxA browserid assertion
            @param session as fxaSession
            @return bundle keys as KeyBundle
        """
        state = None
        if key is not None:
            state = hexlify(sha256(key).digest()[0:16])
        self.__client = SyncClient(bid_assertion, state)
        sync_keys = KeyBundle.fromMasterKey(
                                        key,
                                        "identity.mozilla.com/picl/v1/oldsync")

        # Fetch the sync bundle keys out of storage.
        # They're encrypted with the account-level key.
        keys = self.__decrypt_payload(self.__client.get_record("crypto",
                                                               "keys"),
                                      sync_keys)

        # There's some provision for using separate
        # key bundles for separate collections
        # but I haven't bothered digging through
        # to see what that's about because
        # it doesn't seem to be in use, at least on my account.
        if keys["collections"]:
            print("no support for per-collection key bundles yet sorry :-(")
            return None

        # Now use those keys to decrypt the records of interest.
        bulk_keys = KeyBundle(base64.b64decode(keys["default"][0]),
                              base64.b64decode(keys["default"][1]))
        return bulk_keys

    def get_bookmarks(self, bulk_keys):
        """
            Return bookmarks payload
            @param bulk keys as KeyBundle
            @return [{}]
        """
        bookmarks = []
        for record in self.__client.get_records('bookmarks'):
            record["payload"] = self.__decrypt_payload(record, bulk_keys)
            bookmarks.append(record)
        return bookmarks

    def get_history(self, bulk_keys):
        """
            Return history payload
            @param bulk keys as KeyBundle
            @return [{}]
        """
        history = []
        for record in self.__client.get_records('history'):
            record["payload"] = self.__decrypt_payload(record, bulk_keys)
            history.append(record)
        return history

    def add_bookmark(self, bookmark, bulk_keys):
        payload = self.__encrypt_payload(bookmark, bulk_keys)
        record = {}
        record["modified"] = round(time(), 2)
        record["payload"] = payload
        record["id"] = bookmark["id"]
        self.__client.put_record("bookmarks", record)

    def add_history(self, history, bulk_keys):
        payload = self.__encrypt_payload(history, bulk_keys)
        record = {}
        record["modified"] = round(time(), 2)
        record["payload"] = payload
        record["id"] = history["id"]
        self.__client.put_record("history", record)

    def get_browserid_assertion(self, session,
                                tokenserver_url=TOKENSERVER_URL):
        """
            Get browser id assertion and state
            @param session as fxaSession
            @return (bid_assertion, state) as (str, str)
        """
        bid_assertion = session.get_identity_assertion(tokenserver_url)
        return bid_assertion, session.keys[1]

    @property
    def client(self):
        """
            Get client
        """
        return self.__client

#######################
# PRIVATE             #
#######################
    def __encrypt_payload(self, record, key_bundle):
        """
            Encrypt payload
            @param record as {}
            @param key bundle as KeyBundle
            @return encrypted record payload
        """
        plaintext = json.dumps(record).encode("utf-8")
        # Input strings must be a multiple of 16 in length
        length = 16 - (len(plaintext) % 16)
        plaintext += bytes([length]) * length
        iv = Random.new().read(16)
        aes = AES.new(key_bundle.encryption_key, AES.MODE_CBC, iv)
        ciphertext = base64.b64encode(aes.encrypt(plaintext))
        _hmac = hmac.new(key_bundle.hmac_key,
                         ciphertext,
                         sha256).hexdigest()
        payload = {"ciphertext": ciphertext.decode("utf-8"),
                   "IV": base64.b64encode(iv).decode("utf-8"), "hmac": _hmac}
        return json.dumps(payload)

    def __decrypt_payload(self, record, key_bundle):
        """
            Descrypt payload
            @param record as str (json)
            @param key bundle as KeyBundle
            @return uncrypted record payload
        """
        j = json.loads(record["payload"])
        # Always check the hmac before decrypting anything.
        expected_hmac = hmac.new(key_bundle.hmac_key,
                                 j['ciphertext'].encode("utf-8"),
                                 sha256).hexdigest()
        if j['hmac'] != expected_hmac:
            raise ValueError("HMAC mismatch: %s != %s" % (j['hmac'],
                                                          expected_hmac))
        ciphertext = base64.b64decode(j['ciphertext'])
        iv = base64.b64decode(j['IV'])
        aes = AES.new(key_bundle.encryption_key, AES.MODE_CBC, iv)
        plaintext = aes.decrypt(ciphertext).strip().decode("utf-8")
        # Remove any CBC block padding,
        # assuming it's a well-formed JSON payload.
        plaintext = plaintext[:plaintext.rfind("}") + 1]
        return json.loads(plaintext)


class KeyBundle:
    """
        RFC-5869
    """
    def __init__(self, encryption_key, hmac_key):
        self.encryption_key = encryption_key
        self.hmac_key = hmac_key

    @classmethod
    def fromMasterKey(cls, master_key, info):
        key_material = KeyBundle.HKDF(master_key, None, info, 2 * 32)
        return cls(key_material[:32], key_material[32:])

    def HKDF_extract(salt, IKM, hashmod=sha256):
        """
            Extract a pseudorandom key suitable for use with HKDF_expand
            @param salt as str
            @param IKM as str
        """
        if salt is None:
            salt = b"\x00" * hashmod().digest_size
        return hmac.new(salt, IKM, hashmod).digest()

    def HKDF_expand(PRK, info, length, hashmod=sha256):
        """
            Expand pseudo random key and info
            @param PRK as str
            @param info as str
            @param length as int
        """
        digest_size = hashmod().digest_size
        N = int(math.ceil(length * 1.0 / digest_size))
        assert N <= 255
        T = b""
        output = []
        for i in range(1, N + 1):
            data = T + (info + chr(i)).encode()
            T = hmac.new(PRK, data, hashmod).digest()
            output.append(T)
        return b"".join(output)[:length]

    def HKDF(secret, salt, info, length, hashmod=sha256):
        """
            HKDF-extract-and-expand as a single function.
            @param secret as str
            @param salt as str
            @param info as str
            @param length as int
        """
        PRK = KeyBundle.HKDF_extract(salt, secret, hashmod)
        return KeyBundle.HKDF_expand(PRK, info, length, hashmod)


class TokenserverClient(object):
    """
        Client for the Firefox Sync Token Server.
    """
    def __init__(self, bid_assertion, client_state,
                 server_url=TOKENSERVER_URL):
        """
            Init client
            @param bid assertion as str
            @param client_state as ???
            @param server_url as str
        """
        self.__bid_assertion = bid_assertion
        self.__client_state = client_state
        self.__server_url = server_url

    def get_hawk_credentials(self, duration=None):
        """
            Asks for new temporary token given a BrowserID assertion
            @param duration as str
        """
        authorization = 'BrowserID %s' % self.__bid_assertion
        headers = {
            'Authorization': authorization,
            'X-Client-State': self.__client_state
        }
        params = {}

        if duration is not None:
            params['duration'] = int(duration)

        url = self.__server_url.rstrip('/') + '/1.0/sync/1.5'
        raw_resp = requests.get(url, headers=headers, params=params,
                                verify=True)
        raw_resp.raise_for_status()
        return raw_resp.json()


class SyncClient(object):
    """
        Client for the Firefox Sync server.
    """
    def __init__(self, bid_assertion=None, client_state=None,
                 credentials={}, tokenserver_url=TOKENSERVER_URL):
        """
            Init client
            @param bid assertion as str
            @param client_state as ???
            @param credentials as {}
            @param server_url as str
        """
        if bid_assertion is not None and client_state is not None:
            ts_client = TokenserverClient(bid_assertion, client_state,
                                          tokenserver_url)
            credentials = ts_client.get_hawk_credentials()
        self.__user_id = credentials['uid']
        self.__api_endpoint = credentials['api_endpoint']
        self.__auth = HawkAuth(algorithm=credentials['hashalg'],
                               id=credentials['id'],
                               key=credentials['key'])

    def _request(self, method, url, **kwargs):
        """
            Utility to request an endpoint with the correct authentication
            setup, raises on errors and returns the JSON.
            @param method as str
            @param url as str
            @param kwargs as requests.request named args
        """
        url = self.__api_endpoint.rstrip('/') + '/' + url.lstrip('/')
        raw_resp = requests.request(method, url, auth=self.__auth, **kwargs)
        raw_resp.raise_for_status()

        if raw_resp.status_code == 304:
            http_error_msg = '%s Client Error: %s for url: %s' % (
                raw_resp.status_code,
                raw_resp.reason,
                raw_resp.url)
            raise requests.exceptions.HTTPError(http_error_msg,
                                                response=raw_resp)
        return raw_resp.json()

    def info_collections(self, **kwargs):
        """
            Returns an object mapping collection names associated with the
            account to the last-modified time for each collection.

            The server may allow requests to this endpoint to be authenticated
            with an expired token, so that clients can check for server-side
            changes before fetching an updated token from the Token Server.
        """
        return self._request('get', '/info/collections', **kwargs)

    def info_quota(self, **kwargs):
        """
            Returns a two-item list giving the user's current usage and quota
            (in KB). The second item will be null if the server
            does not enforce quotas.

            Note that usage numbers may be approximate.
        """
        return self._request('get', '/info/quota', **kwargs)

    def get_collection_usage(self, **kwargs):
        """
            Returns an object mapping collection names associated with the
            account to the data volume used for each collection (in KB).

            Note that these results may be very expensive as it calculates more
            detailed and accurate usage information than the info_quota method.
        """
        return self._request('get', '/info/collection_usage', **kwargs)

    def get_collection_counts(self, **kwargs):
        """
            Returns an object mapping collection names associated with the
            account to the total number of items in each collection.
        """
        return self._request('get', '/info/collection_counts', **kwargs)

    def delete_all_records(self, **kwargs):
        """
            Deletes all records for the user
        """
        return self._request('delete', '/', **kwargs)

    def get_records(self, collection, full=True, ids=None, newer=None,
                    limit=None, offset=None, sort=None, **kwargs):
        """
            Returns a list of the BSOs contained in a collection. For example:

            >>> ["GXS58IDC_12", "GXS58IDC_13", "GXS58IDC_15"]

            By default only the BSO ids are returned, but full objects can be
            requested using the full parameter. If the collection does not
            exist, an empty list is returned.

            :param ids:
                a comma-separated list of ids. Only objects whose id is in
                this list will be returned. A maximum of 100 ids may be
                provided.

            :param newer:
                a timestamp. Only objects whose last-modified time is strictly
                greater than this value will be returned.

            :param full:
                any value. If provided then the response will be a list of full
                BSO objects rather than a list of ids.

            :param limit:
                a positive integer. At most that many objects will be returned.
                If more than that many objects matched the query,
                an X-Weave-Next-Offset header will be returned.

            :param offset:
                a string, as returned in the X-Weave-Next-Offset header of a
                previous request using the limit parameter.

            :param sort:
                sorts the output:
                "newest" - orders by last-modified time, largest first
                "index" - orders by the sortindex, highest weight first
                "oldest" - orders by last-modified time, oldest first
        """
        params = kwargs.pop('params', {})
        if full:
            params['full'] = True
        if ids is not None:
            params['ids'] = ','.join(map(str, ids))
        if newer is not None:
            params['newer'] = newer
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        if sort is not None and sort in ('newest', 'index', 'oldest'):
            params['sort'] = sort

        return self._request('get', '/storage/%s' % collection.lower(),
                             params=params, **kwargs)

    def get_record(self, collection, record_id, **kwargs):
        """Returns the BSO in the collection corresponding to the requested id.
        """
        return self._request('get', '/storage/%s/%s' % (collection.lower(),
                                                        record_id), **kwargs)

    def delete_record(self, collection, record_id, **kwargs):
        """Deletes the BSO at the given location.
        """
        try:
            return self._request('delete', '/storage/%s/%s' % (
                collection.lower(), record_id), **kwargs)
        except Exception as e:
            print("SyncClient::delete_record()", e)

    def put_record(self, collection, record, **kwargs):
        """
            Creates or updates a specific BSO within a collection.
            The passed record must be a python object containing new data for
            the BSO.

            If the target BSO already exists then it will be updated with the
            data from the request body. Fields that are not provided will not
            be overwritten, so it is possible to e.g. update the ttl field of a
            BSO without re-submitting its payload. Fields that are explicitly
            set to null in the request body will be set to their default value
            by the server.

            If the target BSO does not exist, then fields that are not provided
            in the python object will be set to their default value
            by the server.

            Successful responses will return the new last-modified time for the
            collection.

            Note that the server may impose a limit on the amount of data
            submitted for storage in a single BSO.
        """
        # XXX: Workaround until request-hawk supports the json parameter. (#17)
        if isinstance(record, six.string_types):
            record = json.loads(record)
        record = record.copy()
        record_id = record.pop('id')
        headers = {}
        if 'headers' in kwargs:
            headers = kwargs.pop('headers')
        headers['Content-Type'] = 'application/json; charset=utf-8'

        return self._request('put', '/storage/%s/%s' % (
            collection.lower(), record_id), data=json.dumps(record),
            headers=headers, **kwargs)
