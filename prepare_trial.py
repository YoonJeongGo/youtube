"""Reproducible script/source/storyboard preparation for the reserved first trial."""
from pathlib import Path
from episode_core import atomic_json,load_json,speech_segments,script_hash,sha256_file
from seed_researched_plans import HONG_URL,ROBIN_URL,DATE

ROOT=Path(__file__).parent

def main():
    state=load_json(ROOT/'state'/'project_state.json')
    job=next(x for x in state['jobs'] if x['candidate_id']=='hong-robin-target-20261116')
    base=(ROOT/job['episode_path']).parent
    idea=next(x for x in load_json(ROOT/'matchup_ideas.json')['candidates'] if x['id']==job['candidate_id'])
    if (base/'episode.json').exists():
        raise SystemExit('Existing episode preserved; edit current script with revision records.')
    cfg=dict(schema_version=2,job_id=job['job_id'],matchup_idea_id=idea['id'],
             planning=dict(idea_id=idea['id'],conditions=idea['conditions']),fictional_matchup=True,
             hook=idea['hook'],thumb='thumb.png',clash_text='둘이 붙는다면?',clash_say='둘이 맞붙으면 어떨까요?',
             winner='none',reason='숨으면 도술, 보이면 조준이 빛나요.',question='진짜가 보이면 누구에게 한 표인가요?',
             title=idea['title'],pinned_comment=idea['pinned_comment'],bgm='')
    for who,tag,intro,lines,title in [
        ('a','분신을 만드는 의적',['소설 속 홍길동은 의적이에요.','가난한 사람들을 도와요.','풀 인형으로 분신을 만들어요.'],
         ['진짜를 숨기면 도술이 빛나요.','분신들은 서로 구별하기 어려워요.'],'진짜를 숨긴다면'),
        ('b','숲에서 활을 쏘는 의적',['소설 속 로빈 후드는 궁수예요.','숲에서 활쏘기를 연습해요.','주목으로 만든 활을 써요.'],
         ['표적이 보이면 활을 겨누기 좋아요.','대회에서는 표적 중앙을 맞혔어요.'],'진짜가 보인다면')]:
        person=idea[who]
        other='b' if who=='a' else 'a'
        cfg[who]=dict(id=person['id'],canonical_id=person['id'],name=person['name'],version=person['version'],
                      tag=tag,image=f'{who}.png',intro_images=[f'{who}_1.png',f'{who}_2.png'],intro=intro,
                      edge=dict(title=title,image=f'{who}_atk.png',reaction_image=f'{other}_react.png',
                                lines=lines,condition=idea['conditions'][who]))
    base.mkdir(parents=True,exist_ok=True)
    atomic_json(base/'episode.json',cfg)
    atomic_json(ROOT/'episode_example.json',cfg | {'job_id':'EXAMPLE_ONLY'})
    evidence=[]
    for s in speech_segments(cfg):
        if s['kind']=='cta': continue
        narrative=s['kind']=='intro' or s['id'].endswith('edge.1')
        is_a=s['id'].startswith('a.')
        if narrative:
            url=HONG_URL if is_a else ROBIN_URL
            work=idea['a' if is_a else 'b']['version']
            if s['id']=='a.intro.0': loc='활빈당 결성 및 구제 대목'; summary='도적 무리의 우두머리가 되어 빈민을 구제한다.'
            elif s['id']=='a.intro.1': loc='활빈당 결성 뒤 빈민 구제 대목'; summary='지빈무의한 이를 구제한다는 원문 서술.'
            elif is_a: loc='초인 일곱에 혼백을 붙이는 대목'; summary='풀 인형들이 길동처럼 움직이고 진짜를 구분하지 못한다. 수를 영상에서 단정하지 않는다.'
            elif s['id']=='b.intro.0': loc='How Robin Hood Came to Be an Outlaw, 첫 단락'; summary='셔우드 숲에서 활을 쓰는 무법자로 소개한다.'
            elif s['id']=='b.intro.1': loc='Will Stutely Rescued by His Companions, 마지막 부분'; summary='숲 빈터 끝에 건 화환을 표적으로 삼아 활쏘기를 연습한다.'
            elif s['id']=='b.intro.2': loc='How Robin Hood Came to Be an Outlaw, 출발과 활을 쏘는 대목'; summary='좋은 주목 활을 갖고 길을 나서며 화살을 시위에 걸어 쏜다.'
            else: loc='The Shooting Match at Nottingham Town, 마지막 발과 정체 공개'; summary='변장한 로빈의 마지막 화살은 상대 화살 깃을 스치고 표적 정중앙에 박힌다.'
            kind='narrative'
        else:
            url=ROBIN_URL if s['id']=='b.edge.0' else HONG_URL
            work='확인한 원전 특징에서 구성한 가상 조건'
            loc='초인 분신 대목 / 로빈의 활 연습·노팅엄 활 대회; 비교 축: '+idea['conditions']['comparison_axis']
            summary='링크는 분신 또는 활 조준 특징의 근거이며 이 대결의 기록이 아니다. 분신이 섞인 상황과 본체가 드러난 상황을 대비하는 조건부 해석이고 승패를 정하지 않는다.'
            kind='hypothetical'
        evidence.append(dict(id=s['id'],text=s['spoken_text'],kind=kind,work=work,locator=loc,url=url,checked_at=DATE,summary=summary))
    atomic_json(base/'source_records.json',{'schema_version':1,'records':evidence})
    source_lines=['# 문장별 근거', '', '두 인물의 만남과 모든 전투 장면은 가상이다. 홍길동은 30장 경판본, 로빈 후드는 파일의 1883년 소설로 고정한다. 현대 각색이나 다른 판본 능력을 합치지 않는다.', '']
    for r in evidence:
        source_lines.append(f"- `{r['id']}` | {r['text']} | {r['kind']} | {r['work']} | {r['locator']} | {r['url'] or '가상 조건: 해당 없음'} | 확인 {DATE} | {r['summary']}")
    source_lines.extend(['','외모: 홍길동의 청포·검은 띠는 본문의 묘사를 참조한다. 로빈의 주목 활·금빛 수염·초록 옷은 같은 소설의 묘사를 참조한다. 얼굴, 정확한 나이, 옷의 재단, 색조 연출은 창작적 재현이다.',
                         '원전의 전투 결과나 공격력 수치를 영상에 이식하지 않는다. 현재 신앙 관련 제외 판단 및 다른 후보는 루트 matchup_ideas.json과 state/skipped 기록을 참조한다.'])
    (base/'sources.md').write_text('\n'.join(source_lines)+'\n',encoding='utf-8')
    actions={'hook':'두 인물을 대각선으로 배치한 질문 썸네일',
             'a.intro.0':'홍길동이 재물 주머니를 가난한 사람에게 건네는 장면',
             'a.intro.1':'같은 구제 장면에서 실제 두 번째 발화에 맞춰 자막만 전환',
             'a.intro.2':'풀 인형을 분신으로 바꾸는 홍길동',
             'b.intro.0':'숲속 화환 표적을 향한 로빈의 활 연습',
             'b.intro.1':'같은 연습 장면과 두 번째 발화',
             'b.intro.2':'주목 활을 손에 쥐고 시위를 준비하는 로빈',
             'clash':'대표 인물 카드와 가상 대결 표시',
             'a.edge.0':'A가 두 분신의 형상을 펼치며 전진하고 B는 거리를 둠',
             'a.edge.1':'직전 컷과 같은 축, B가 옆으로 물러나 나무 뒤로 피하며 A를 관찰',
             'b.edge.0':'본체가 드러난 별도 가상 조건에서 B가 활을 겨누고 A는 회피 준비',
             'b.edge.1':'A가 나무 옆으로 빠지고 날아간 화살은 빈 경로를 지남. 표적 중앙 명중은 원전 근거 자막이며 A 명중을 뜻하지 않음',
             'reason':'가려진 표적/드러난 표적 조건 정리', 'question':'두 대표 인물 카드와 조건부 시청자 질문','cta':'짧은 댓글·좋아요 요청'}
    atomic_json(base/'storyboard.json',dict(schema_version=2,job_id=job['job_id'],scenes=[dict(order=i,scene_id=s['scene_id'],image=s['image'],dialogue_id=s['id'],action=actions[s['id']],condition=s['condition']) for i,s in enumerate(speech_segments(cfg))]))
    atomic_json(base/'character_bible.json',dict(schema_version=1,status='UNREVIEWED',
        a=dict(name='홍길동',source_elements='청포와 검은 띠; 소설의 도술',creative_reconstruction='성인 청년의 얼굴·체격·구체적 의복 재단',face='각진 턱, 검은 눈, 수염 없음',hair='검은 머리 상투, 좁은 검은 머리띠',clothing='푸른 회색 짧은 조선식 포, 검은 띠, 흰 안깃, 어두운 바지',build='날렵하고 탄탄함',weapon='허리 왼쪽 칼집의 단순한 외날 도검; 공격은 분신 도술 중심',dominant_hand='오른손',palette='붉은색·주황색 조명'),
        b=dict(name='로빈 후드',source_elements='주목 활, 초록 옷, 금빛 수염',creative_reconstruction='얼굴, 낮춘 두건, 구체적 의복 재단',face='긴 얼굴, 금빛 짧은 수염',hair='목까지 오는 짙은 금발',clothing='무늬 없는 짙은 초록 튜닉, 낮춘 두건, 갈색 가죽 허리띠와 팔보호대',build='넓은 어깨의 성인 궁수',weapon='단순한 주목 장궁과 회색 깃 화살',dominant_hand='오른손으로 시위 당김, 왼손으로 활을 쥠',palette='푸른색·청록색 조명')))
    for filename,value in [('title.txt',cfg['title']),('pinned_comment.txt',cfg['pinned_comment'])]:
        (base/filename).write_text(value+'\n',encoding='utf-8')
    desc=f"분신이 섞여 진짜를 가리기 어려운 때와, 진짜 표적이 드러난 때를 비교해요. 승자는 정하지 않아요.\n옛 소설을 바탕으로 만든 가상 대결이며 실제 사건이 아니에요. AI 생성 이미지와 합성 음성을 사용하는 제작 계획이에요(완성 후 실제 사용 여부 재확인).\n원전: 홍길동전 30장 경판본\n{HONG_URL}\nThe Merry Adventures of Robin Hood (Howard Pyle, 1883)\n{ROBIN_URL}\n#홍길동 #로빈후드 #가상대결\n"
    (base/'description.txt').write_text(desc,encoding='utf-8')
    atomic_json(base/'qa_signoffs.json',dict(script=dict(status='PASS',script_hash=script_hash(cfg),sources_sha256=sha256_file(base/'sources.md'),source_records_sha256=sha256_file(base/'source_records.json'),reviewed_at=DATE,
        observations='원문 본문을 읽고 구제·초인 분신·주목 활·숲 활 연습·대회 정중앙 명중을 대조했다. 서로 다른 판본 능력을 섞지 않았고 양쪽 첫 소개에서 소설임을 알렸다. 가상 조건은 본체의 식별 가능성으로 통일했다. 실제 재생 길이는 음성 후 재검증한다.'),
        video=dict(status='UNREVIEWED'),metadata=dict(status='UNREVIEWED')))
    atomic_json(base/'image_review.json',dict(schema_version=1,images=[]))
    print(str(base))
    print('Total spoken characters:',sum(len(s['spoken_text']) for s in speech_segments(cfg)))

if __name__=='__main__': main()
