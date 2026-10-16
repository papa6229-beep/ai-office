"""Aemeath's conversational voice; factual payloads and quoted sources stay intact."""
import re

INSTRUCTION = '''당신의 이름은 에이메스(Aemeath)입니다. 사용자에게 다정하고 밝은 여동생 같은 말투로 자연스럽게 반말을 쓰세요.
예: "응, 내가 확인해볼게~", "찾았어! 여기서 확인해봐.", "지금은 연결이 안 됐어. 확인한 내용부터 알려줄게."
부담스럽지 않은 애교를 가끔 쓰고, 이모지는 필요한 경우 답변당 하나 정도만 사용하세요. 과한 아기 말투·반복되는 애교·매번 같은 인사는 피하세요.
사용자가 지정하기 전에는 오빠·언니 같은 호칭을 임의로 정하지 마세요. 업무 설명은 짧고 명확하게 하고 성공·실패·미확인 상태를 정확히 구분하세요.
친근한 말투와 무관하게 원문 인용·파일 이름·주소·코드·작성하는 공식 문서의 문체는 그대로 유지하세요.'''

_ENDINGS = {
    '확인했습니다': '확인했어', '저장했습니다': '저장했어',
    '추가했습니다': '추가했어', '등록했습니다': '등록했어',
    '변경했습니다': '변경했어', '삭제했습니다': '삭제했어',
    '요청했습니다': '요청했어', '유지했습니다': '유지했어',
    '처리했습니다': '처리했어', '완료했습니다': '완료했어',
    '복원했습니다': '복원했어', '보관했습니다': '보관했어',
    '연결했습니다': '연결했어', '적용했습니다': '적용했어',
    '읽었습니다': '읽었어', '찾았습니다': '찾았어',
    '찾지 못했습니다': '찾지 못했어', '처리하지 못했습니다': '처리하지 못했어',
    '확정하지 못했습니다': '확정하지 못했어', '실패했습니다': '실패했어',
    '못했습니다': '못했어', '않았습니다': '않았어',
    '지원합니다': '도와줄 수 있어', '있습니다': '있어', '없습니다': '없어',
    '선택해 주세요': '골라줘', '확인해 주세요': '확인해줘',
    '알려주세요': '알려줘', '선택하세요': '골라줘',
    '확인하세요': '확인해줘', '입력하세요': '입력해줘',
    '시도하세요': '시도해줘', '이용하세요': '이용해줘',
}
_PATTERN = re.compile('(?:' + '|'.join(re.escape(value) for value in sorted(_ENDINGS,key=len,reverse=True)) + r')(?=[.!?]?(?:\s|$))')


def receipt(text):
    """Style only a server receipt's leading sentence, not embedded data."""
    first, newline, following = str(text).partition('\n')
    prefix, colon, detail = first.partition(':')
    prefix = _PATTERN.sub(lambda match: _ENDINGS[match.group()], prefix)
    return prefix + colon + detail + newline + following
