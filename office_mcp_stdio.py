"""MCP stdio relay. No tools execute in this child; the office owns execution."""
import json
import os
import sys
import urllib.request
from urllib.parse import urlsplit


def main():
    endpoint=os.environ.get('AI_OFFICE_MCP_ENDPOINT','')
    token=os.environ.get('AI_OFFICE_MCP_TOKEN','')
    url=urlsplit(endpoint)
    if url.scheme!='http' or url.hostname!='127.0.0.1' or url.path!='/rpc' or not token:
        return 2
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for line in sys.stdin.buffer:
        if len(line)>2*1024*1024:return 2
        try:
            message=json.loads(line)
            if not isinstance(message,dict):continue
            request=urllib.request.Request(endpoint,data=json.dumps(message,ensure_ascii=False).encode('utf-8'),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
            with opener.open(request,timeout=660) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:return 2
            if 'id' in message:
                # Only protocol messages go to stdout; no diagnostics/credentials.
                sys.stdout.buffer.write(raw+b'\n');sys.stdout.buffer.flush()
        except Exception:
            if isinstance(locals().get('message'),dict) and 'id' in message:
                error={'jsonrpc':'2.0','id':message['id'],'error':{'code':-32603,'message':'Office tool connection interrupted; inspect the office ledger before retrying effects.'}}
                sys.stdout.buffer.write(json.dumps(error).encode('utf-8')+b'\n');sys.stdout.buffer.flush()
    return 0


if __name__=='__main__':raise SystemExit(main())
