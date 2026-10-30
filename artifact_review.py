"""Read local deliverables without browser navigation or executing their content."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

REVISION='2026-10-29-local-artifact-v1'
ROOT=Path(__file__).resolve().parent


def guidance():
    return {'revision':REVISION,'python':sys.executable,'entry':str(ROOT/'artifact_review.py'),
            'command':'inspect --path <absolute local path>',
            'rule':'로컬 HTML·이미지·영상·PDF는 local_artifact_inspect 또는 이 읽기 전용 실행기로 먼저 확인합니다. 앱 브라우저에 file://를 전달하지 않습니다. 읽기/구문 확인은 렌더링·육안 확인과 구별합니다. 차단된 파일을 HTTP 서버·다른 브라우저·보안 설정 변경으로 우회하지 않습니다.'}


def local_path(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('확인할 파일의 전체 경로가 필요합니다.')
    value=value.strip()
    if value.lower().startswith('file:'):
        uri=urlsplit(value)
        if uri.netloc not in ('','localhost'):raise ValueError('원격 파일 주소는 로컬 파일로 열지 않습니다.')
        value=unquote(uri.path)
        if re.match(r'^/[A-Za-z]:/',value):value=value[1:]
    elif re.match(r'^[A-Za-z][A-Za-z0-9+.-]*://',value):
        raise ValueError('웹 주소는 웹 작업이며 로컬 파일 검사 대상이 아닙니다.')
    path=Path(value)
    if not path.is_absolute():raise ValueError('상대 경로 대신 전체 로컬 파일 경로를 사용하세요.')
    import file_operations
    path=file_operations._guard(path)
    if not path.is_file():raise ValueError('확인할 실제 파일을 찾지 못했습니다.')
    return path


class Summary(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title=False;self.titles=[];self.videos=0;self.images=0;self.scripts=0
    def handle_starttag(self,tag,attrs):
        if tag=='title':self.title=True
        if tag=='video':self.videos+=1
        if tag=='img':self.images+=1
        if tag=='script':self.scripts+=1
    def handle_endtag(self,tag):
        if tag=='title':self.title=False
    def handle_data(self,data):
        if self.title:self.titles.append(data[:200])


def inspect(value):
    path=local_path(value);before=path.stat();digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    result={'revision':REVISION,'path':str(path),'name':path.name,'bytes':before.st_size,
            'sha256':digest.hexdigest(),'mode':'code','model_calls':0,
            'route':'local_file_tool','browser_navigation':False,'content_executed':False,
            'visual_verified':False,'visual_review':'pending',
            'open_in_codex':{'target':{'type':'file','path':str(path)}},
            'file_link':'['+path.name+'](<'+path.as_posix()+'>)'}
    if path.suffix.lower() in {'.html','.htm'}:
        if before.st_size>64*1024*1024:raise ValueError('HTML은64MiB 이하 파일만 이 경로에서 읽습니다.')
        parser=Summary();parser.feed(path.read_text(encoding='utf-8-sig',errors='replace'));parser.close()
        result['html']={'title':' '.join(parser.titles)[:200],'video_elements':parser.videos,
                        'image_elements':parser.images,'script_elements':parser.scripts,
                        'read_only':True,'rendered':False}
    if path.stat().st_size!=before.st_size or path.stat().st_mtime_ns!=before.st_mtime_ns:
        raise ValueError('검사 중 파일이 변경됐습니다. 완료로 처리하지 않습니다.')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect']);parser.add_argument('--path',required=True)
    args=parser.parse_args();print(json.dumps(inspect(args.path),ensure_ascii=False))


if __name__=='__main__':main()
