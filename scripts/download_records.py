#!/usr/bin/env python3

try:
    import script_env
except ImportError:
    pass
import argparse
import asyncio
import PyMajsoul.majsoul_pb2 as pb
from PyMajsoul.majsoul_msjrpc import Lobby
from PyMajsoul.msjrpc import MSJRpcChannel
from google.protobuf.json_format import MessageToJson
from google.protobuf.json_format import MessageToDict
import hmac
import hashlib
import getpass
import uuid 
# aiohttp requires python 3.7 and above to run.
import aiohttp
import random
import json
import os
import base64

parser = argparse.ArgumentParser(description="Download records from majsoul server.")
parser.add_argument("--output", dest="jsondir", type=str, required=True,
                    help="Convert the downloaded records to json format, and output to the folder, folder path is the value of this argument.")
parser.add_argument("--output-pb", dest="pbdir", type=str,
                    help="Simply dump the proto message as byte stream, and output to the folder, folder path is the value of this argument.")
parser.add_argument("--memoize", dest="memoize_file", type=str,
                    help="Indicates a file to store all downloaded files. If this argument was not given, then program will try to download all records. This is useful if want to move the downloaded files to other places.")
parser.add_argument("--no-new-records", "-N", action="store_true",
                    help="Decode existing records only. No new records will be downloaded.")

args = parser.parse_args()

print(args)


async def manual_login(lobby):
    print("Manual Logging in")
    req = pb.ReqLogin()
    req.account = input("Username:").encode()
    pwd = getpass.getpass()
    req.password = hmac.new(b'lailai', pwd.encode(), hashlib.sha256).hexdigest()
    req.device.device_type = 'pc'
    req.device.browser = 'safari'
    uuid_key = str(uuid.uuid1())
    req.random_key = uuid_key
    req.client_version = lobby.version
    req.gen_access_token = True
    req.currency_platforms.append(2)
    res = await lobby.login(req)
    token = res.access_token
    res.access_token = "MASKED FOR PRINTING"
    print("Login Result:")
    print(res)
    with open(".majsoul", "w") as f:
        print("Saving access token")
        json.dump({"random_key":uuid_key, "access_token": token}, f)

async def relogin(lobby):
    if not os.path.exists(".majsoul"):
        print("No access token present")
        return False
    with open(".majsoul") as f:
        print("Reading access token")
        token_d = json.load(f)

    print("Checking access token")
    req = pb.ReqOauth2Check()
    req.access_token = token_d["access_token"]
    res = await lobby.oauth2Check(req)
    if not res.has_account:
        print("Invalid access token")
        return False
    print("Automatic logging in")
    req = pb.ReqOauth2Login()
    req.access_token = token_d["access_token"]
    req.device.device_type = 'pc'
    req.device.browser = 'safari'
    req.random_key = token_d["random_key"]
    req.client_version = lobby.version
    req.currency_platforms.append(2)
    res = await lobby.oauth2Login(req)
    res.access_token = "MASKED FOR PRINTING"
    print("Login Result:")
    print(res)
    return True

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://majsoul.union-game.com/0/version.json") as res:
            version = await res.json()
            version = version["version"]
        async with session.get("https://majsoul.union-game.com/0/v{}/config.json".format(version)) as res:
            config = await res.json()
            url = config["ip"][0]["region_urls"]["mainland"]
        async with session.get(url + "?service=ws-gateway&protocol=ws&ssl=true") as res:
            servers = await res.json()
            servers = servers["servers"]
            server = random.choice(servers)
            endpoint = "wss://{}/".format(server)

    print("Chosen endpoint: {}".format(endpoint))
    channel = MSJRpcChannel(endpoint)

    lobby = Lobby(channel)
    lobby.version = version
    await channel.connect()
    print("Connection estabilished")

    result = await relogin(lobby)
    if not result:
        await manual_login(lobby)

    print("Fetching record list")
    records = []
    current = 1
    step = 30
    while True:
        req = pb.ReqGameRecordList()
        req.start = current
        req.count = step
        print("Fetching {} record ids from {}".format(step, current))
        res = await lobby.fetchGameRecordList(req)
        records.extend([r.uuid for r in res.record_list])
        if len(res.record_list) < step:
            break
        current += step
    print("Found {} records".format(len(records)))

    total = len(records)
    for i, r in enumerate(records):
        path = os.path.join(args.jsondir, r)
        if os.path.exists(path):
            print("({}/{})Skipping existing {}".format(i + 1, total, i))
            continue

        req = pb.ReqGameRecord()
        req.game_uuid = r 
        print("({}/{})Fetching {}".format(i + 1, total, r))
        res = await lobby.fetchGameRecord(req)
        with open(path, "w") as f:
            print("({}/{})Saving {}".format(i + 1, total, r))
            f.write(MessageToJson(res))

    await channel.close()
    print("Connection closed")
    await decode_records(records)


async def decode_records(records):
    total = len(records)
    print("Fetching details")
    async with aiohttp.ClientSession() as session:
        for i, r in enumerate(records):
            print("({}/{})Processing {}".format(i + 1, total, r))
            path = os.path.join(args.jsondir, r)
            with open(path) as f:
                data = json.load(f)
            if "data" in data:
                print("({}/{})Data present in {}, skipping".format(i + 1, total, r))
                continue
            if "dataUrl" in data:
                url = data["dataUrl"]
                print("({}/{})Fetching details for {}".format(i + 1, total, r))
                async with session.get(url) as res:
                    details = await res.read()
                print("({}/{})Fetched details  for {}".format(i + 1, total, r))
                data["data"] = base64.b64encode(details).decode()
                with open(path, "w") as f:
                    print("({}/{})Saving {}".format(i + 1, total, r))
                    json.dump(data, f, indent=2, ensure_ascii=False)
                continue
            print("({}/{})Neither data or dataUrl in {}, skipping".format(i + 1, total, r))

    print("Decoding details")

    for i, r in enumerate(records):
        print("({}/{})Processing {}".format(i + 1, total, r))
        path = os.path.join(args.jsondir, r)
        with open(path) as f:
            data = json.load(f)
        if "details" in data:
            print("({}/{})Details present in {}, skipping".format(i + 1, total, r))
            continue
        blob = base64.b64decode(data['data'])

        wrapper = pb.Wrapper()
        wrapper.ParseFromString(blob)
        assert wrapper.name == '.lq.GameDetailRecords'
        records_data = wrapper.data

        records = pb.GameDetailRecords()
        records.ParseFromString(records_data)
        records = records.records

        results = []
        for record in records:
            wrapper = pb.Wrapper()
            wrapper.ParseFromString(record)
            name = wrapper.name.replace(".lq.", "")
            cls = getattr(pb, name)
            obj = cls()
            obj.ParseFromString(wrapper.data)
            result = MessageToDict(obj)
            result['@type'] = name
            results.append(result)
        data["details"] = results

        with open(path, "w") as f:
            print("({}/{})Saving {}".format(i + 1, total, r))
            json.dump(data, f, indent=2, ensure_ascii=False)





if args.no_new_records:
    asyncio.run(decode_records(os.listdir(args.jsondir)))
else:
    asyncio.run(main())
