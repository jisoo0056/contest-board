"""공모전 수집기.

하루 한 번 GitHub Actions에서 실행된다.
  1) 공모전 모음 사이트(링커리어, 콘테스트코리아, 위비티)에서 공모전·대외활동 수집
  2) 기관 게시판(web)에서 공모전 글 제목/링크 수집
  3) 관심 분야(문화기획·문화정책·브랜드·공공문제)만 남기고 분류
  4) docs/data/contests.json, status.json 갱신
  5) 새 공모전이 있으면 이메일 발송
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import re
import smtplib
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.date()
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NO_DEADLINE_DAYS = 45  # 마감일을 못 찾은 글은 이 기간이 지나면 마감 처리

# ---------------------------------------------------------------- 분류 규칙
# 형태: 위에서부터 먼저 맞는 것으로 분류
FORM_RULES = [
    ("서포터즈", r"서포터즈|기자단|홍보\s*대사|앰배서더|엠버서더|크리에이터\s*(단|모집)|리포터|모니터링\s*단|모니터\s*단|홍보단|에디터\s*모집"),
    ("대외활동", r"대외\s*활동|참여단|자문단|기획단|평가단|운영단|디자인단|체험단|탐방|봉사단|활동가\s*모집|청년\s*위원|정책\s*참여|멘토링|아카데미|부트캠프|프로그램\s*참가"),
    ("결과물", r"영상|UCC|숏폼|쇼츠|릴스|디자인|포스터|일러스트|사진|웹툰|캐릭터|로고|엠블럼|굿즈|카드\s*뉴스|광고\s*제작|콘텐츠\s*제작|AI\s*(영상|이미지|콘텐츠)|뮤직\s*비디오|브이로그|패키지"),
    ("기획서", r"기획서|기획안|제안서|사업\s*계획|마케팅\s*(전략|기획)|전략\s*(공모|제안)|논문|리포트|보고서|케이스\s*스터디|PT\s*(경진|대회)|기획\s*(공모|경진|대회)|정책\s*연구|솔버톤|해커톤|경진\s*대회"),
    ("아이디어 제안", r"아이디어|제안|네이밍|이름\s*(공모|짓기)|명칭|슬로건|캐치\s*프레이즈|카피|개선\s*과제|해결\s*방안|국민\s*생각"),
]
# 모음 사이트의 분야 이름으로 형태를 정할 때(제목으로 못 정했을 때)
FORM_BY_CATEGORY = [
    ("서포터즈", r"서포터즈|기자단|홍보대사"),
    ("대외활동", r"대외활동|봉사|탐방|참여단|체험|강연|멘토|교육|인턴"),
    ("결과물", r"영상|UCC|사진|디자인|웹툰|캐릭터"),
    ("기획서", r"논문|리포트|광고|마케팅|기획\s*•\s*홍보"),
    ("아이디어 제안", r"기획/아이디어|아이디어|네이밍|슬로건"),
]

# 분야: 여러 개에 동시에 들 수 있음. 하나도 안 맞으면 수집하지 않음(관심 밖)
FIELD_RULES = {
    "문화기획": r"문화|예술|축제|페스티벌|공연|전시|관광|콘텐츠|박물관|미술관|도서관|국가\s*유산|문화재|유산|로컬|지역\s*(활성화|브랜딩|콘텐츠|살리기)|공간\s*(기획|활용)|K-?\s*(컬처|POP|콘텐츠)|한류|한글|전통|영화|출판|독서|아트|뮤지엄",
    "문화정책": r"(문화|예술|관광|콘텐츠|체육|국가\s*유산|문화재|한류|저작권|미디어)\S{0,8}\s*(정책|제도|규제|법|발전\s*방안|활성화\s*방안|진흥)|문화\s*체육\s*관광부|문체부|문화\s*정책|예술\s*정책|문화\s*도시|지역\s*문화|문화\s*(격차|복지|누리|향유|다양성)",
    "브랜드·마케팅": r"브랜드|브랜딩|마케팅|광고|홍보|네이밍|슬로건|캐치\s*프레이즈|카피|BI|CI|캠페인|PR|SNS|서포터즈|앰배서더|홍보\s*대사|굿즈|이름\s*(공모|짓기)|명칭",
    "공공문제·정책제안": r"정책|국민\s*(제안|생각|참여)|시민\s*(제안|참여|아이디어)|공공|사회\s*(문제|혁신|가치)|ESG|지역\s*(사회|문제|소멸|균형|상생)|혁신|적극\s*행정|규제|민원|저출생|저출산|고령|기후|탄소|환경|안전|복지|공익|불편|개선|행정|청년\s*(정책|문제|참여)|소셜\s*(벤처|임팩트)|지속\s*가능|인구",
}
# 링커리어 '관심분야' 이름 → 분야
FIELD_BY_INTEREST = {
    "문화기획": r"문화|예술|여행|관광|공연|전시",
    "브랜드·마케팅": r"마케팅|광고|홍보|브랜드",
    "공공문제·정책제안": r"정치|사회|법률|환경|공공|행정",
}
CULTURE_ORG = (r"문화\s*체육\s*관광부|국가\s*유산청|문화재청|한국문화예술위원회|지역문화진흥원|콘텐츠진흥원|문화관광연구원|"
               r"예술경영지원센터|문화예술교육진흥원|문화재단|관광공사|국립\S*(박물관|미술관|극장|도서관)|한국관광공사|저작권위원회|국악원")
PUBLIC_ORG = r"부$|청$|처$|원$|공단|공사|재단|위원회|시청|구청|군청|도청|광역시|특별시|특별자치|정부|공공|진흥원|연구원|센터|협회|군$|구$|시$|도$"

# 관심 없는 분야: 제목에 있으면 제외 (디자인·사진·UCC·문학 등)
EXCLUDE = (
    r"신춘\s*문예|백일장|글짓기|수필|시\s*(공모|낭송)|소설|시나리오|문학|독후감|수기|에세이|동화|웹\s*소설|"
    r"웹툰|만화|이모티콘|캐릭터|일러스트|사진|포토|그림|사생|미술\s*대회|회화|서예|공예\s*작품|작가\s*공모|전시\s*작가|"
    r"UCC|숏폼|쇼츠|릴스|뮤직\s*비디오|영상|유튜브|브이로그|애니메이션|영화제|단편\s*영화|드라마|drama|film|"
    r"미술(?!관)|공예|전람회|미디어\s*아트|아트\s*토이|전시\s*지원|음원|BGM|작곡|작사|스토리텔러|희곡|SF\s*어워드|경연|"
    r"이벤트|팔로우|경시\s*대회|토론\s*대회|레터링|폰트|타이포|디자인\s*(\S+\s*)?(공모|대회|어워드)|작품\s*(공모|전)|"
    r"디자인\s*(공모|콘테스트|어워드|대회)|포스터\s*(공모|디자인)|로고\s*(디자인|공모)|엠블럼|패키지\s*디자인|"
    r"가요제|노래|댄스|콩쿠르|합창|오디션|밴드|음악\s*(경연|대회)|연주|"
    r"요리|레시피|뷰티|메이크업|스포츠|체육\s*대회|마라톤|e\s*스포츠|게임\s*(개발|잼)|코딩|알고리즘|SW\s*(개발|경진)|"
    r"소프트웨어|해킹|로봇|드론|과학\s*(경진|탐구)|수학|발명|특허|장학생|장학금|공모\s*결과|수상작|결과\s*발표|당첨자|"
    r"채용|인턴십|입사|공채|교육생|수강생|강사|레지던시|입주"
)
# 대외활동 중 관심 밖 (교육·봉사·캠프·체험·취업 프로그램)
ACTIVITY_EXCLUDE = (r"봉사|멘토|멘티|캠프|실무\s*(체험|경험)|포트\s*폴리오|포폴|직무|콘서트|토크|교육|스쿨|강의|수강|부트\s*캠프|취업|코칭|세미나|특강|클래스|과외|학습|스터디|"
                    r"어학|연수|해외\s*(탐방|체험)|체험단|시식|리뷰어|모니터\s*요원|알바|아르바이트|파트너스|크루\s*상시|"
                    r"동아리|운영\s*위원회|학생\s*위원|면접|자격증|코딩|개발자|IT|AI\s*(교육|활용\s*교육)|헬스|다이어트|뷰티|화장품|패션|"
                    r"게임|e스포츠|스포츠|의료|병원|약국|금융\s*(교육|체험)|주식|투자|코인|부동산")
# 청소년·어린이만 참가할 수 있는 공모전은 제외
YOUTH_ONLY = r"청소년|중학생|고등학생|초등학생|어린이|중·고|초·중·고|학생부"

# 기관 게시판에서 '공모전'으로 볼 글 (지원사업 공모, 기관 선정 공모 등은 제외)
WEB_INCLUDE = (r"공모전|아이디어\s*(공모|제안|경진|대회)|정책\s*(제안|아이디어)|국민\s*(제안|참여단|생각)|서포터즈|기자단|"
               r"홍보\s*대사|네이밍|슬로건|명칭\s*공모|이름\s*공모|대학생|청년\s*(참여단|기자단|서포터즈|공모|위원)|공모\s*대전|경진\s*대회")
WEB_EXCLUDE = (r"지원\s*사업|사업\s*공모|단체\s*(공모|모집)|기관\s*(선정|모집|공모)|위탁|용역|입찰|결과|발표|선정|심사|"
               r"수요\s*조사|장관상|대통령상|정부\s*포상|대관|위원\s*(모집|위촉)|평가\s*위원|후보|정산|기금|보조금|"
               r"공모\s*사업|레지던시|입주|협약|설명회|연장|변경|취소|정정|재공고|교육\s*과정|아카데미")
NAV_WORDS = {"공모전", "공지사항", "새소식", "더보기", "공모", "공지", "모집"}

# 참가 자격
TARGET_GENERAL = r"제한\s*(없|무)|누구나|일반인|일반\s*국민|직장인|전\s*국민|국민\s*누구나|성인|시민|도민|구민|군민"
TARGET_UNIV = r"대학생|대학원생|대학\s*\(\s*원\s*\)\s*생|대학\s*재학|휴학생|대학교\s*재학"
TARGET_YOUTH = r"청년|만\s*(1[89]|2\d)\s*세?\s*[~\-]\s*(만\s*)?(3[0-9])\s*세|만\s*3[49]\s*세\s*이하|MZ"

# 개인/팀
TEAM_BOTH = (r"개인\s*(또는|및|/|·|,|혹은|or|과|와|이나|\s)\s*(팀|단체|그룹)|(팀|단체)\s*(또는|및|/|·|,|혹은|or|과|와)\s*개인|"
             r"개인\s*\(\s*\d\s*인\s*\)|팀\s*(참가|참여|응모)\s*가능|개인\s*및\s*\d\s*인|1\s*인\s*~\s*\d\s*인|1\s*~\s*\d\s*인")
TEAM_ONLY = (r"팀\s*(단위|으로|당|별)\s*(참가|참여|지원|응모|구성)|[2-9]\s*~\s*\d\s*인\s*(1\s*)?(팀|조)|[2-9]\s*인\s*(이상|이내|이하)\s*(의\s*)?(팀|조)|"
             r"팀\s*\(\s*[2-9]|팀\s*구성\s*(필수|필요)|팀원\s*\d")
TEAM_IND = (r"개인\s*(만|단위)\s*(참가|참여|응모|지원)?|개인\s*(참가|참여|응모)\s*(만|가능)?(?!\s*(또는|및|/|·|,))|1\s*인\s*1\s*(건|작품|편|안)|"
            r"팀\s*(참가|참여|응모)\s*(불가|불가능)|단체\s*(참가|참여|응모)\s*(불가|불가능)")


def classify_form(text: str, cats: str = "") -> str | None:
    for f, pat in FORM_RULES:
        if re.search(pat, text, re.I):
            return f
    for f, pat in FORM_BY_CATEGORY:
        if cats and re.search(pat, cats):
            return f
    return None


# '문화'가 들어가도 문화예술과 무관한 말
NOT_CULTURE = r"(건강|낚시|조직|음주|안전|교통|기업|금융|소비|운전|식|보행|기부|나눔|캠핑|반려|직장|병영|군|학교|회식|보안|청렴|과학\s*기술|과학|디지털|양성평등|인권|노사)\s*문화"


def fields_of(title: str, interests: str = "", org: str = "", form: str | None = None) -> list[str]:
    t = re.sub(NOT_CULTURE, " ", title)
    activity = form in ("서포터즈", "대외활동")
    if activity:  # '서포터즈'라는 말만으로 브랜드 분야로 보지 않음
        t = re.sub(r"서포터즈|앰배서더|엠버서더|홍보\s*대사|SNS", " ", t, flags=re.I)
    out = [k for k, pat in FIELD_RULES.items() if re.search(pat, t, re.I)]
    for k, pat in FIELD_BY_INTEREST.items():  # 서포터즈·대외활동은 태그가 느슨해서 제목으로만 판단
        if interests and not activity and re.search(pat, interests) and k not in out:
            out.append(k)
    org = (org or "").strip()
    # 공공기관이 여는 아이디어/기획 공모는 대부분 공공문제 해결 제안
    if form in ("아이디어 제안", "기획서") and org and re.search(PUBLIC_ORG, org) and "공공문제·정책제안" not in out:
        out.append("공공문제·정책제안")
    # 문화 분야의 정책·제도 제안, 또는 문화 관련 공공기관의 아이디어 공모는 문화정책
    if "문화정책" not in out and (
            ("문화기획" in out and re.search(r"정책|제도|개선|제안|활성화\s*방안|발전\s*방안|혁신", t))
            or (not activity and re.search(CULTURE_ORG, org))):
        out.append("문화정책")
    return [k for k in FIELD_RULES if k in out]


def targets_of(text: str) -> list[str]:
    t = []
    if re.search(TARGET_UNIV, text):
        t.append("대학생")
    if re.search(TARGET_GENERAL, text):
        t.append("일반인")
    if re.search(TARGET_YOUTH, text):
        t.append("청년")
    if re.search(YOUTH_ONLY, text):
        t.append("청소년")
    return t


def team_of(text: str) -> str | None:
    """'개인·팀' / '팀' / '개인' / None(알 수 없음)"""
    text = re.sub(r"\s+", " ", text or "")
    if re.search(TEAM_BOTH, text):
        return "개인·팀"
    team, ind = re.search(TEAM_ONLY, text), re.search(TEAM_IND, text)
    if team and ind:
        return "개인·팀"
    if team:
        return "팀"
    if ind:
        return "개인"
    return None


def unwanted(c: dict) -> str | None:
    """관심 밖이면 이유를 돌려준다."""
    title = c["title"]
    if re.search(EXCLUDE, title, re.I):
        return "제외 분야"
    if c.get("form") in ("서포터즈", "대외활동") and re.search(ACTIVITY_EXCLUDE, title, re.I):
        return "교육·봉사·체험 프로그램"
    if re.search(r"청소년|중학생|고등학생|초등학생|어린이", title) and not re.search(r"대학|청년|일반|국민|시민|지도사|성인", title):
        return "청소년 대상"
    tg = c.get("targets") or []
    if tg and set(tg) <= {"청소년"}:
        return "청소년 대상"
    if not c.get("fields"):
        return "관심 분야 아님"
    return None


def norm(s: str) -> str:
    s = re.sub(r"재단법인|사단법인|\((재|사|주)\)|㈜", "", s or "")
    return re.sub(r"[\s()（）\[\]·.\-_]", "", s)


def mkid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def html_text(s: str) -> str:
    s = re.sub(r"<(script|style).*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</tr>", "\n", s, flags=re.I)
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", s))


DATE_RE = r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})"


def to_date(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def iso(d):
    return d.isoformat() if isinstance(d, date) else d


def find_deadline(text: str) -> date | None:
    m = re.search(r"~\s*\(?\s*" + DATE_RE, text)
    if m:
        return to_date(*m.groups())
    m = re.search(r"(마감|까지|접수\s*기간)[^0-9]{0,8}" + DATE_RE, text)
    if m:
        return to_date(*m.groups()[1:])
    m = re.search(r"~\s*\(?\s*(\d{1,2})\s*[./월]\s*(\d{1,2})", text)
    if m:
        d = to_date(TODAY.year, *m.groups())
        if d and d < TODAY - timedelta(days=180):
            d = to_date(TODAY.year + 1, *m.groups())
        return d
    return None


def find_post_date(text: str) -> date | None:
    ds = [to_date(*m.groups()) for m in re.finditer(DATE_RE, text)]
    ds = [d for d in ds if d and d <= TODAY + timedelta(days=1)]
    return max(ds) if ds else None


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})


def fetch(url: str, **kw) -> str:
    r = SESSION.get(url, timeout=30, **kw)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def base_item(src: dict, **kw) -> dict:
    item = {"source": src["name"], "source_id": src["id"], "targets": [], "team": None,
            "start": None, "deadline": None, "reward": None, "org": None, "status_hint": None}
    item.update(kw)
    return item


# ---------------------------------------------------------------- 링커리어
LINKAREER_GQL = "https://api.linkareer.com/graphql"
LINKAREER_QUERY = """query ($typeID: ID!, $page: Int!) {
  activities(filterBy: {activityTypeID: $typeID, status: OPEN}, pagination: {page: $page, pageSize: 100},
             orderBy: {field: CREATED_AT, direction: DESC}) {
    totalCount
    nodes { id title organizationName organizationType recruitStartAt recruitCloseAt
            targets { name } categories { name } interests { name } tenThousandUnitOfReward
            detailText { text } }
  }
}"""


def _ms(v):
    return datetime.fromtimestamp(int(v) / 1000, KST).date() if v else None


def collect_linkareer(src: dict) -> list[dict]:
    """링커리어 공개 목록 API. type 3 = 공모전, 1 = 대외활동"""
    out = []
    for type_id, kind in (("3", "공모전"), ("1", "대외활동")):
        max_pages = src.get("pages", {}).get(kind, 5)
        for page in range(1, max_pages + 1):
            r = SESSION.post(LINKAREER_GQL, timeout=60, json={
                "query": LINKAREER_QUERY, "variables": {"typeID": type_id, "page": page}})
            r.raise_for_status()
            j = r.json()
            if j.get("errors"):
                raise RuntimeError(json.dumps(j["errors"], ensure_ascii=False)[:300])
            nodes = j["data"]["activities"]["nodes"]
            for n in nodes:
                title = clean(n["title"])
                cats = " ".join(c["name"] for c in n.get("categories") or [])
                ints = " ".join(c["name"] for c in n.get("interests") or [])
                tnames = " ".join(c["name"] for c in n.get("targets") or [])
                detail = html_text((n.get("detailText") or {}).get("text") or "")
                form = classify_form(title, cats)
                if kind == "대외활동" and form not in ("서포터즈", "대외활동"):
                    form = classify_form(title, cats) if re.search(r"서포터즈|기자단|홍보대사", cats) else "대외활동"
                org = clean(n.get("organizationName"))
                reward = n.get("tenThousandUnitOfReward")
                out.append(base_item(
                    src, id=f"linkareer-{n['id']}", title=title, url=f"https://linkareer.com/activity/{n['id']}",
                    org=org, org_type=n.get("organizationType"), form=form, kind=kind,
                    categories=cats, interests=ints,
                    targets=targets_of(f"{tnames} {title}"),
                    team=team_of(detail) or (("개인" if form == "서포터즈" else None)),
                    start=iso(_ms(n.get("recruitStartAt"))), deadline=iso(_ms(n.get("recruitCloseAt"))),
                    reward=(f"총 {int(reward):,}만원" if reward and str(reward).isdigit() and int(reward) > 0 else None),
                ))
            if len(nodes) < 100:
                break
            time.sleep(0.7)
    return out


# ---------------------------------------------------------------- 콘테스트코리아
CK_LIST = "https://www.contestkorea.com/sub/list.php"


def _ck_range(text: str, reg: date | None):
    """'11.18~11.29' → (2026-11-18, 2026-11-29). 연도는 등록일 기준."""
    m = re.search(r"(\d{1,2})\.(\d{1,2})\s*~\s*(\d{1,2})\.(\d{1,2})", text or "")
    if not m:
        return None, None
    y = (reg or TODAY).year
    s = to_date(y, m.group(1), m.group(2))
    if s and reg and s < reg - timedelta(days=120):
        s = to_date(y + 1, m.group(1), m.group(2))
    e = to_date(s.year if s else y, m.group(3), m.group(4))
    if s and e and e < s:
        e = to_date(s.year + 1, m.group(3), m.group(4))
    return s, e


def collect_contestkorea(src: dict) -> list[dict]:
    out, seen = [], set()
    for gbn, bcode, label, pages in src.get("boards", []):
        for page in range(1, pages + 1):
            h = fetch(CK_LIST, params={"displayrow": 60, "int_gbn": gbn, "Txt_sGn": 1, "Txt_key": "all",
                                       "Txt_bcode": bcode, "page": page})
            soup = BeautifulSoup(h, "html.parser")
            lis = soup.select(".list_style_2 > ul > li")
            if not lis:
                break
            for li in lis:
                a = li.select_one(".title a")
                if not a:
                    continue
                href = a.get("href") or ""
                m = re.search(r"str_no=(\d+)", href)
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                txt = a.select_one(".txt")
                title = clean(txt.get_text(" ") if txt else a.get_text(" "))
                cat = clean((a.select_one(".category") or a).get_text(" ")) if a.select_one(".category") else label
                host = li.select_one(".host")
                host_t = clean(host.get_text(" ")) if host else ""
                org = re.search(r"주최\s*\.\s*(.*?)(?:대상\s*\.|$)", host_t)
                tgt = re.search(r"대상\s*\.\s*(.*)$", host_t)
                reg = to_date(m.group(1)[:4], m.group(1)[4:6], m.group(1)[6:8])
                step = li.select_one(".step-1")
                s, e = _ck_range(step.get_text(" ") if step else "", reg)
                cond = li.select_one(".d-day .condition")
                cond = clean(cond.get_text()) if cond else ""
                form = classify_form(title, cat if gbn == 1 else "")
                if gbn == 2 and form not in ("서포터즈", "대외활동"):
                    form = "서포터즈" if re.search(r"서포터즈|기자단", cat + label) else "대외활동"
                out.append(base_item(
                    src, id=f"ck-{m.group(1)}", title=title, url=urljoin(CK_LIST, href),
                    org=org.group(1).strip(" ,") if org else None, form=form, categories=cat,
                    kind="대외활동" if gbn == 2 else "공모전",
                    targets=targets_of(f"{tgt.group(1) if tgt else ''} {title}"),
                    start=iso(s), deadline=iso(e), posted=iso(reg),
                    status_hint=("upcoming" if "예정" in cond else "open" if re.search(r"접수중|마감임박", cond)
                                 else "closed" if cond else None),
                ))
            time.sleep(0.6)
    return out


def contestkorea_detail(c: dict) -> None:
    t = clean(html_text(fetch(c["url"])))
    m = re.search(r"참가대상\s*(.*?)\s*접수기간", t)
    if m:
        tg = targets_of(m.group(1) + " " + c["title"])
        c["targets"] = sorted(set(c.get("targets", [])) | set(tg), key=TARGET_ORDER.index)
    m = re.search(r"접수기간\s*" + DATE_RE + r"\s*~\s*" + DATE_RE, t)
    if m:
        c["start"] = iso(to_date(*m.groups()[:3]))
        c["deadline"] = iso(to_date(*m.groups()[3:]))
    m = re.search(r"시상내역\s*(.*?)\s*(홈페이지|접수방법|참가비용)", t)
    if m and m.group(1).strip():
        c["reward"] = m.group(1).strip()[:60]
    c["team"] = c.get("team") or team_of(t)


# ---------------------------------------------------------------- 위비티
WEV = "https://www.wevity.com/"


def _wev_rows(params: dict):
    soup = BeautifulSoup(fetch(WEV, params=params), "html.parser")
    for li in soup.select("ul.list > li"):
        if "top" in (li.get("class") or []):
            continue
        a = li.select_one(".tit a")
        if not a:
            continue
        m = re.search(r"ix=(\d+)", a.get("href") or "")
        if m:
            yield m.group(1), li, a


def collect_wevity(src: dict) -> list[dict]:
    out = {}
    for cidx, pages in src.get("categories", []):
        for gp in range(1, pages + 1):
            rows = list(_wev_rows({"c": "find", "s": 1, "gub": 1, "cidx": cidx, "gp": gp}))
            if not rows:
                break
            for ix, li, a in rows:
                if ix in out:
                    continue
                for sp in a.select("span"):
                    sp.extract()
                title = clean(a.get_text(" "))
                sub = clean((li.select_one(".sub-tit") or li).get_text(" ")) if li.select_one(".sub-tit") else ""
                cats = re.sub(r"^분야\s*:\s*", "", sub)
                org = clean((li.select_one(".organ") or a).get_text(" ")) if li.select_one(".organ") else None
                day = clean((li.select_one(".day") or a).get_text(" ")) if li.select_one(".day") else ""
                dd = re.search(r"D\s*-\s*(\d+)", day)
                deadline = TODAY + timedelta(days=int(dd.group(1))) if dd else (TODAY if re.search(r"D-?day|오늘", day, re.I) else None)
                hint = ("upcoming" if "예정" in day else "closed" if "마감" in day and "임박" not in day
                        else "open" if deadline else None)
                form = classify_form(title, cats)
                out[ix] = base_item(
                    src, id=f"wevity-{ix}", title=title,
                    url=f"https://www.wevity.com/?c=find&s=1&gub=1&gbn=view&ix={ix}",
                    org=org, form=form, categories=cats, kind="대외활동" if form in ("서포터즈", "대외활동") else "공모전",
                    targets=targets_of(title), deadline=iso(deadline), status_hint=hint,
                )
            time.sleep(0.6)
    # 참가 대상 목록에 나오면 그 대상으로 표시
    for cidx, label, pages in src.get("targets", []):
        for gp in range(1, pages + 1):
            rows = list(_wev_rows({"c": "find", "s": 1, "gub": 2, "cidx": cidx, "gp": gp}))
            if not rows:
                break
            for ix, _, _ in rows:
                if ix in out and label not in out[ix]["targets"]:
                    out[ix]["targets"].append(label)
            time.sleep(0.6)
    return list(out.values())


# ---------------------------------------------------------------- 기관 게시판
def collect_web(src: dict) -> list[dict]:
    url = src["url"]
    soup = BeautifulSoup(fetch(url), "html.parser")
    inc = re.compile(src.get("include") or WEB_INCLUDE, re.I)
    out, seen = [], set()
    for a in soup.find_all("a"):
        text = clean(a.get_text(" "))
        if len(text) < 8 or text in NAV_WORDS:
            continue
        if not inc.search(text) or re.search(WEB_EXCLUDE, text):
            continue
        row = a.find_parent(["tr", "li"]) or a.parent
        row_text = clean(row.get_text(" ")) if row else text
        if len(row_text) > 400:
            row_text = text
        href = (a.get("href") or "").strip()
        link = url if (not href or href.startswith(("#", "javascript"))) else urljoin(url, href)
        title = re.sub(r"^(새글|NEW|공지)\s*", "", text)
        title = re.sub(r"\s*20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}\.?\s*$", "", title).strip()
        key = link if link != url else title
        if key in seen:
            continue
        seen.add(key)
        form = classify_form(title)
        out.append(base_item(
            src, id=mkid(src["id"], key), title=title, url=link, org=src.get("org") or src["name"],
            form=form, kind="대외활동" if form in ("서포터즈", "대외활동") else "공모전",
            targets=targets_of(title), deadline=iso(find_deadline(text)),
            posted=iso(find_post_date(row_text.replace(text, " ")) or find_post_date(text)),
        ))
    return out


DEADLINE_KEY = r"접수\s*마감|마감\s*기한|마감\s*일시?|접수\s*기간|공모\s*기간|모집\s*기간|응모\s*기간|제출\s*기간|신청\s*기간"


def web_detail(c: dict) -> None:
    """게시판 글 상세에서 접수기간, 참가 대상, 개인/팀을 찾는다."""
    t = clean(html_text(fetch(c["url"])))
    for k in re.finditer(DEADLINE_KEY, t):
        w = t[k.end(): k.end() + 80]
        ds = sorted({d for d in (to_date(*m.groups()) for m in re.finditer(DATE_RE, w)) if d})
        for m2 in re.finditer(r"~\s*\(?\s*(\d{1,2})\s*[./월]\s*(\d{1,2})(?!\d)", w):
            if ds:
                d2 = to_date(ds[0].year, *m2.groups())
                if d2 and ds[0] <= d2 <= ds[0] + timedelta(days=150):
                    ds.append(d2)
        if ds:
            c["start"] = c.get("start") or (iso(min(ds)) if len(ds) > 1 else None)
            c["deadline"] = c.get("deadline") or iso(max(ds))
            break
    m = re.search(r"(참가|응모|공모|모집|신청)\s*(대상|자격)\s*[:：]?\s*(.{0,120})", t)
    if m:
        c["targets"] = sorted(set(c.get("targets", [])) | set(targets_of(m.group(3))), key=TARGET_ORDER.index)
    c["team"] = c.get("team") or team_of(t)


# ---------------------------------------------------------------- 병합/저장
TARGET_ORDER = ["대학생", "일반인", "청년", "청소년"]


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def state_of(c: dict) -> str:
    """'open' 접수 중 / 'upcoming' 접수 시작 전 / 'closed' 마감"""
    t = TODAY.isoformat()
    if c.get("deadline") and c["deadline"] < t:
        return "closed"
    if c.get("start") and c["start"] > t:
        return "upcoming"
    if c.get("status_hint") == "upcoming" and not c.get("start"):
        return "upcoming"
    if c.get("status_hint") == "closed" and not c.get("deadline"):
        return "closed"
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", c.get("title", ""))]
    if years and max(years) < TODAY.year:
        return "closed"
    if not c.get("deadline"):
        base = c.get("posted") or c.get("first_seen")
        if base and base < (TODAY - timedelta(days=NO_DEADLINE_DAYS)).isoformat():
            return "closed"
    return "open"


def tnorm(t: str) -> str:
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】|<[^>]*>|「|」|『|』|20\d{2}\s*년?|제\s*\d+\s*회", "", t)
    return re.sub(r"[^가-힣A-Za-z0-9]", "", t).lower()


MERGE_KEYS = ("targets", "team", "start", "deadline", "reward", "org", "form")


def send_mail(new_items: list[dict], failed: list[str]):
    host, user, pw, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO"))
    if not (host and user and pw and to):
        print("메일 설정 없음 → 발송 생략")
        return
    site = os.environ.get("SITE_URL", "")
    esc = htmllib.escape
    order = {f: i for i, (f, _) in enumerate(FORM_RULES)}
    rows = []
    for c in sorted(new_items, key=lambda x: (x.get("state") == "upcoming", order.get(x.get("form"), 9),
                                             x.get("deadline") or "9999")):
        when = (f'{c["start"][5:].replace("-", ".")} 접수 시작' if c.get("state") == "upcoming" and c.get("start")
                else f'~{c["deadline"][5:].replace("-", ".")}' if c.get("deadline") else "-")
        who = "·".join(t for t in c.get("targets", []) if t != "청소년") or "-"
        rows.append(
            f'<tr><td style="padding:6px 10px;color:#555">{esc(c.get("form") or "미분류")}</td>'
            f'<td style="padding:6px 10px"><a href="{esc(c["url"])}">{esc(c["title"])}</a>'
            f'<div style="color:#777;font-size:12px">{esc(c.get("org") or "")} · {esc(" · ".join(c.get("fields", [])))}</div></td>'
            f'<td style="padding:6px 10px;color:#555">{esc(who)}</td>'
            f'<td style="padding:6px 10px;color:#555">{esc(c.get("team") or "-")}</td>'
            f'<td style="padding:6px 10px;color:#555;white-space:nowrap">{esc(when)}</td></tr>')
    fail = (f'<p style="color:#b45309">⚠ 수집 실패: {esc(", ".join(failed))}</p>' if failed else "")
    body = (f'<div style="font-family:sans-serif"><h2>새 공모전 {len(new_items)}건</h2>'
            f'<table style="border-collapse:collapse;font-size:14px">'
            f'<tr style="background:#f3f4f6"><th>형태</th><th>공모전</th><th>대상</th><th>개인/팀</th><th>접수</th></tr>'
            + "".join(rows) + "</table>" + fail
            + (f'<p><a href="{esc(site)}">공모전 모아보기 사이트 열기</a></p>' if site else "") + "</div>")
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = f"[공모전 모아보기] 새 공모전 {len(new_items)}건 ({TODAY:%m/%d})"
    msg["From"] = user
    msg["To"] = to
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [to], msg.as_string())
    print(f"메일 발송: {len(new_items)}건")


COLLECTORS = {"linkareer": collect_linkareer, "contestkorea": collect_contestkorea,
              "wevity": collect_wevity, "web": collect_web}
PRIORITY = {"linkareer": 0, "contestkorea": 1, "wevity": 2, "web": 3}


def main():
    only = sys.argv[1:]  # 테스트용: 특정 출처 id만
    sources = load("sources.json", {"sources": []})["sources"]
    old = {c["id"]: c for c in load("contests.json", {"contests": []})["contests"]}
    prev_ok = {k for k, v in load("status.json", {}).get("sources", {}).items() if v.get("ok")}
    status, found = {}, []

    for s in sources:
        fn = COLLECTORS.get(s.get("type"))
        if not fn or (s["type"] == "web" and not s.get("url")) or (only and s["id"] not in only):
            continue
        try:
            items = fn(s)
            for c in items:
                c["parser"] = s["type"]
            found += items
            status[s["id"]] = {"ok": True, "count": len(items)}
            print(f"OK   {s['name']}: {len(items)}건")
        except Exception as e:
            status[s["id"]] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            print(f"FAIL {s['name']}: {e}")
        time.sleep(1)

    # 분류 + 관심 밖 제외
    kept, dropped = [], 0
    for c in found:
        c["fields"] = fields_of(c["title"], c.get("interests", ""), c.get("org") or "", c.get("form"))
        if unwanted(c):
            dropped += 1
            continue
        kept.append(c)

    # 같은 공모전이 여러 사이트에 있으면 하나로 합치고 빠진 정보는 채운다
    kept.sort(key=lambda c: PRIORITY.get(c["parser"], 9))
    by_key: dict[str, dict] = {}
    for c in kept:
        k = tnorm(c["title"])
        twin = by_key.get(k) if len(k) >= 6 else None
        if not twin and len(k) >= 12:
            twin = next((v for kk, v in by_key.items() if len(kk) >= 12 and (k in kk or kk in k)), None)
        if twin:
            for f in MERGE_KEYS:
                if not twin.get(f) and c.get(f):
                    twin[f] = c[f]
            if c.get("targets"):
                twin["targets"] = sorted(set(twin["targets"]) | set(c["targets"]), key=TARGET_ORDER.index)
            twin.setdefault("also", []).append({"source": c["source"], "url": c["url"]})
            continue
        by_key[k] = c

    today = TODAY.isoformat()
    merged = dict(old)
    new_items = []
    for c in by_key.values():
        prev = old.get(c["id"])
        if prev:
            c["first_seen"] = prev["first_seen"]
            c["initial"] = prev.get("initial", False)
            for f in ("team", "start", "deadline", "reward"):
                c[f] = c.get(f) or prev.get(f)
            c["detail_checked"] = prev.get("detail_checked", False)
        else:
            c["first_seen"] = today
            # 처음 수집하는 출처의 기존 글은 '새 공모전'으로 치지 않는다
            c["initial"] = c["source_id"] not in prev_ok
            c["detail_checked"] = False
        c["last_seen"] = today
        merged[c["id"]] = c

    contests = list(merged.values())
    # 규칙이 바뀌어 이제 관심 밖이 된 예전 글 정리
    for c in contests:
        c["fields"] = fields_of(c["title"], c.get("interests", ""), c.get("org") or "", c.get("form"))
    contests = [c for c in contests if not unwanted(c)]

    # 상세 페이지에서 빠진 정보 채우기 (새로 잡힌 글 위주, 하루 최대 150건)
    checked = 0
    for c in contests:
        if c.get("detail_checked") or c["last_seen"] != today or checked >= 150:
            continue
        if c["parser"] not in ("contestkorea", "web"):
            continue
        if state_of(c) == "closed":
            continue
        checked += 1
        try:
            (contestkorea_detail if c["parser"] == "contestkorea" else web_detail)(c)
        except Exception as e:
            print(f"상세 확인 실패 {c['url']}: {e}")
        c["detail_checked"] = True
        time.sleep(0.5)

    for c in contests:
        c["state"] = state_of(c)
    # 처음 볼 때부터 이미 마감된 글은 담지 않는다
    contests = [c for c in contests if c["id"] in old or c["state"] != "closed"]
    # 마감 후 90일 지난 글은 정리
    cutoff = (TODAY - timedelta(days=90)).isoformat()
    contests = [c for c in contests if not (c["state"] == "closed" and (c.get("deadline") or c["last_seen"]) < cutoff)]
    for c in contests:
        if c["first_seen"] == today and not c["initial"] and c["state"] != "closed" and c["id"] not in old:
            new_items.append(c)

    save("contests.json", {"updated": NOW.isoformat(timespec="minutes"), "contests": contests})
    if only:
        prev_status = load("status.json", {}).get("sources", {})
        prev_status.update(status)
        status = prev_status
    save("status.json", {"updated": NOW.isoformat(timespec="minutes"), "sources": status})

    failed = [s["name"] for s in sources if status.get(s["id"], {}).get("ok") is False]
    print(f"총 {len(contests)}건 (관심 밖 {dropped}건 제외), 새 공모전 {len(new_items)}건, 실패 {len(failed)}곳")
    if new_items and not only:
        try:
            send_mail(new_items, failed)
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
