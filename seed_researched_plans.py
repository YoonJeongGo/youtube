"""One-time researched plan seed (2026-11-16). Does not generate or publish media."""
from pathlib import Path
from episode_core import atomic_json
from plan_matchups import CRITERIA

DATE = '2026-11-16'
HONG_URL = 'https://ko.wikisource.org/wiki/홍길동전_(30장_경판본)'
ROBIN_URL = 'https://www.gutenberg.org/files/964/964-h/964-h.htm'
SIEG_URL = 'https://www.gutenberg.org/cache/epub/1151/pg1151-images.html'

def source(url, work, locator, summary):
    return dict(url=url,work=work,locator=locator,summary=summary,checked_at=DATE,read=True)

HONG = dict(id='novel:hong_gil_dong',name='홍길동',version='홍길동전 30장 경판본, 위키문헌 원문 전사',aliases=['홍 길동'])
ROBIN = dict(id='legend:robin_hood',name='로빈 후드',version='The Merry Adventures of Robin Hood, Howard Pyle 1883, Gutenberg #964',aliases=['로빈후드'])
SIEG = dict(id='epic:siegfried',name='지크프리트',version='Nibelungenlied, Daniel B. Shumway 1909 번역, Gutenberg #1151')
HS = source(HONG_URL,'홍길동전 30장 경판본','활빈당 결성 뒤 초인 일곱을 만드는 대목 (본문 전사 322~335행)',
            '가난한 이를 구제하고 풀 인형에 혼백을 붙여 진짜를 구별할 수 없는 길동들을 만든다.')
RS = source(ROBIN_URL,'The Merry Adventures of Robin Hood','How Robin Hood Came to Be an Outlaw; The Shooting Match at Nottingham Town; Will Stutely Rescued by His Companions',
            '셔우드의 무법자이며 주목 활을 쓴다. 숲에서 화환 표적을 쏘며 연습하고 대회에서 상대 화살 깃을 스치며 표적 한가운데를 맞힌다.')
SS = source(SIEG_URL,'Nibelungenlied, Shumway 번역','Adventure III; Adventure XV',
            '발뭉 검을 쓰며 용의 피로 몸이 단단해졌다는 이야기와 어깨 사이 보리수 잎 자리가 서술된다. 다른 판본 능력을 추가하지 않는다.')

def allowed(id,a,b,premise,question,conditions,hook,title,comment,sources,reasons):
    return dict(id=id,a=a,b=b,format='one_on_one',eligibility=dict(status='ALLOWED',
        reason='현대 각색 캐릭터가 아닌 명시한 옛 문학 판본의 두 인물이다. 현존 신앙 대상으로 확인되지 않았으며 국가 우열을 다루지 않는다. 원전 내용을 개인의 기술과 가상 조건으로만 비교한다.',
        evidence=[s['url'] for s in sources]),premise=premise,core_question=question,conditions=conditions,
        hook=hook,title=title,pinned_comment=comment,sources=sources,
        criteria={k:{'pass':True,'reason':v} for k,v in zip(CRITERIA,reasons)},unresolved=[])

def build_pool():
    primary = allowed('hong-robin-target-20261116',HONG,ROBIN,
        '사람을 속이는 분신술과 표적을 맞히는 활솜씨를 조준 가능 여부로 비교한다.',
        '명궁은 진짜 홍길동을 골라 맞힐 수 있을까요?',
        dict(a='분신이 섞여 진짜 표적을 가리기 어려운 상황',b='분신이 없고 진짜 표적이 드러난 상황',comparison_axis='진짜 표적 식별 가능 여부',fictional=True),
        '분신 앞에서도 활이 통할까요?',
        '홍길동 vs 로빈 후드 | 분신 앞에서도 명궁의 활이 통할까?',
        '분신이 섞인 상황과 진짜가 드러난 상황, 누구의 강점이 더 빛날까요? 한 표와 이유를 남겨 주세요.',[HS,RS],
        ['속임수와 정밀 조준의 충돌을 한 문장으로 설명할 수 있다.','두 인물 모두 소설 속 무법자/의적이지만 분신과 활이 대비된다.',
         '판본을 고정하고 전사 본문 및 소설 본문을 직접 읽었다.','진짜가 가려진 때와 드러난 때라는 동일 축을 사용한다.',
         '구제·분신 생성·숲속 활 연습·주목 활 준비·도술 공격/회피·사격/회피를 분리한다.',
         '분신을 골라 맞힐 수 있는가라는 단일 질문과 짧은 문장으로 구성한다.','기존 제작 이력이 없어 첫 비교 축으로 채택한다. 후속 후보에서는 표적 식별 축을 반복하지 않는다.'])
    second = allowed('hong-sieg-deception-20261116',HONG,SIEG,
        '분신으로 시선을 흐리는 도술과 공격을 견디는 몸의 대비를 다룬다.',
        '상대를 찾기 어려운 싸움과 정면 승부는 어떻게 다를까요?',
        dict(a='분신으로 본체 위치를 숨긴 상황',b='본체가 드러나 정면 공격을 받는 상황',comparison_axis='본체 노출 여부',fictional=True),
        '몸이 단단해도 분신을 가릴까요?',
        '홍길동 vs 지크프리트 | 분신과 단단한 몸이 맞선다면?',
        '본체를 숨길 때와 드러낼 때, 어느 쪽 강점이 더 쓸모있을까요? 이유도 알려 주세요.',[HS,SS],
        ['회피·기만과 내구력의 대비가 분명하다.','적을 혼란시키는 술법과 강인한 몸이라는 다른 수단이다.','두 작품의 해당 본문을 확인했다.',
         '본체 노출을 공통 축으로 비교한다.','분신 생성과 검·방어 모습을 서로 다른 컷으로 구성할 수 있다.',
         '본체 노출이라는 한 조건에 집중하면 짧게 설명 가능하다.','첫 후보와 분신이 겹치므로 후순위다. 연속 채택 전에 다른 인물 후보를 추가 조사한다.'])
    third = allowed('robin-sieg-distance-20261116',ROBIN,SIEG,
        '활로 조준할 거리와 검이 닿는 거리에서 쓸 수 있는 수단을 비교한다.',
        '활을 쏠 틈과 검을 휘두를 틈 중 무엇이 중요할까요?',
        dict(a='서로 떨어져 활을 조준할 여유가 있는 상황',b='붙어서 발뭉 검이 닿는 상황',comparison_axis='교전 거리',fictional=True),
        '검이 닿기 전에 조준할 수 있을까요?',
        '로빈 후드 vs 지크프리트 | 활의 거리, 검의 거리',
        '조준할 거리가 있을 때와 검이 닿을 때, 누구에게 한 표인가요? 이유를 남겨 주세요.',[RS,SS],
        ['서로 다른 무기 운용 거리를 비교한다.','활과 검이 대비된다.','주목 활·명중 장면과 발뭉 사용 대목을 읽었다.',
         '원거리와 근거리를 같은 축으로 대비한다. 화살의 피부 관통은 주장하지 않는다.',
         '활 시위 당기기/엄폐와 검 휘두르기/뒤로 피하기를 구성할 수 있다.',
         '무기 거리라는 직관적인 질문으로 제한한다.','분신 축과 다르지만 첫 편의 로빈이 반복되므로 연속 제작 시 다른 후보 조사 우선.'])
    blocks = [
        dict(id='queue-lubu-zeus-hold',a=dict(id='history:lu_bu',name='여포',version='미선정'),b=dict(id='myth:zeus',name='제우스',version='그리스 전승'),format='one_on_one',
             eligibility=dict(status='BLOCKED',reason='현재 종교 단체의 제우스 의례가 확인되어 현재 신앙 대상 제외 규칙 적용.',evidence=['https://www.ysee.gr/greekwheel.html'])),
        dict(id='queue-thor-wukong-hold',a=dict(id='myth:thor',name='토르',version='북유럽 전승'),b=dict(id='novel:sun_wukong',name='제천대성',version='미선정'),format='one_on_one',
             eligibility=dict(status='BLOCKED',reason='현재 아사트루 단체가 토르 신앙과 의례를 설명하므로 제외. 현대 만화판 토르로 바꾸지 않음.',evidence=['https://www.asatru.org/aboutasatru.php'])),
        dict(id='queue-achilles-sieg-hold',a=dict(id='epic:achilles',name='아킬레우스',version='일리아스 검토'),b=SIEG,format='one_on_one',
             eligibility=dict(status='BLOCKED',reason='현대 신앙 실천자의 아킬레우스 숭배 진술이 있다. 조직적 범위는 미확인이나 현재 신앙 대상 제외 기준을 확실히 통과하지 못해 이번 제작에서 보류.',evidence=['https://www.reddit.com/r/Hellenism/comments/18kzi03/is_it_possible_to_worship_achilles/'])),
        dict(id='queue-kings-unresearched',a=dict(id='history:gwanggaeto',name='광개토대왕',version='미선정'),b=dict(id='history:alexander_iii',name='알렉산더',version='미선정'),format='one_on_one',
             eligibility=dict(status='PENDING',reason='개인의 1대1 기록과 현존 신앙 관련 여부를 추가 확인해야 함. 군대 간 전쟁으로 임의 변환하지 않음.',evidence=[])),
    ]
    return dict(schema_version=1,checked_at=DATE,aliases={},candidates=blocks[:3]+[primary]+blocks[3:]+[second,third],
                expansion_candidates=[dict(format='three_way',status='HOLD',reason='세 참가자를 그대로 보존할 별도 형식 필요'),
                                      dict(format='army_vs_army',status='HOLD',reason='병력·작전·시대 비교용 별도 제작 형식 필요')])

if __name__ == '__main__':
    target = Path(__file__).parent/'matchup_ideas.json'
    if target.exists():
        raise SystemExit('Existing researched pool preserved. Update individual plans after research.')
    atomic_json(target,build_pool())
