"""Динамические карточки МЕРЫ: настоящие данные, векторные формы, локальный PNG."""
import calendar
import hashlib
import json
import math
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1200, 720
NAVY = '#06152F'
PANEL = '#102C50'
TEAL = '#18E1D0'
BLUE = '#4187FF'
WHITE = '#F4F8FF'
MUTED = '#A4BBD5'
CORAL = '#FF817F'
MONTHS = ('ЯНВАРЬ', 'ФЕВРАЛЬ', 'МАРТ', 'АПРЕЛЬ', 'МАЙ', 'ИЮНЬ', 'ИЮЛЬ', 'АВГУСТ', 'СЕНТЯБРЬ', 'ОКТЯБРЬ', 'НОЯБРЬ', 'ДЕКАБРЬ')


@lru_cache(maxsize=100)
def font(size, weight=600):
    path = ROOT / 'assets/fonts/Manrope.ttf'
    if not path.exists():
        raise FileNotFoundError('Не найден assets/fonts/Manrope.ttf. Восстановите шрифт из архива проекта.')
    result = ImageFont.truetype(str(path), size)
    result.set_variation_by_axes([weight])
    return result


def model_hash(model):
    return hashlib.sha256(('mera-cards-v5:' + json.dumps(model, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()


def wrap(draw, value, size, width, weight=500, max_lines=3):
    words = str(value).split()
    lines, current = [], ''
    for word in words:
        proposed = (current + ' ' + word).strip()
        if draw.textlength(proposed, font=font(size, weight)) > width and current:
            lines.append(current)
            current = word
        else:
            current = proposed
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip('., ') + '…'
    for i, line in enumerate(lines):
        while draw.textlength(line, font=font(size, weight)) > width and len(line) > 1:
            line = line[:-2] + '…'
        lines[i] = line
    return lines


class Canvas:
    def __init__(self):
        self.image = Image.new('RGB', (WIDTH, HEIGHT), NAVY)
        draw = ImageDraw.Draw(self.image)
        for y in range(HEIGHT):
            t = y / HEIGHT
            draw.line((0, y, WIDTH, y), fill=(5 + int(3 * t), 18 + int(10 * t), 42 + int(17 * t)))
        glow = Image.new('RGB', self.image.size, (0, 0, 0))
        g = ImageDraw.Draw(glow)
        g.ellipse((680, 20, 1250, 650), fill=(7, 30, 72))
        g.ellipse((-380, 410, 470, 1240), fill=(1, 28, 47))
        glow = glow.filter(ImageFilter.GaussianBlur(80))
        from PIL import ImageChops
        self.image = ImageChops.add(self.image, glow)
        self.d = ImageDraw.Draw(self.image)
        self.d.arc((855, -255, 1505, 395), 30, 330, fill='#14355D', width=2)
        self.d.arc((-340, 415, 430, 1185), 180, 355, fill='#0D3A54', width=2)

    def text(self, xy, value, size=28, color=WHITE, weight=600, anchor=None):
        self.d.text(xy, str(value), font=font(size, weight), fill=color, anchor=anchor)

    def pill(self, xy, value, color=TEAL, size=23):
        x, y = xy
        value=wrap(self.d,value,size,474,weight=700,max_lines=1)[0]
        w = self.d.textlength(str(value), font=font(size, 700)) + 38
        self.d.rounded_rectangle((x, y, x + w, y + 48), radius=16, fill=PANEL, outline='#24516D', width=1)
        self.text((x + 19, y + 8), value, size, color, 700)

    def paragraph(self,xy,value,size=24,width=490,lines=2,color=MUTED,weight=500):
        for index,line in enumerate(wrap(self.d,value,size,width,weight=weight,max_lines=lines)):
            self.text((xy[0],xy[1]+index*(size+8)),line,size,color,weight)

    def metric(self,xy,label,value,width=224,color=TEAL):
        x,y=xy
        self.d.rounded_rectangle((x,y,x+width,y+80),radius=18,fill='#0C2745',outline='#28516D')
        self.text((x+16,y+10),label,17,MUTED,700)
        size=32
        while self.d.textlength(str(value),font=font(size,800))>width-32 and size>18:
            size-=2
        self.text((x+16,y+32),value,size,color,800)

    def logo(self, cx, cy, scale=1):
        s = scale
        self.d.polygon([(cx - 25*s, cy - 27*s), (cx + 7*s, cy), (cx - 25*s, cy + 27*s)], fill=BLUE)
        self.d.polygon([(cx + 27*s, cy - 27*s), (cx - 8*s, cy), (cx + 27*s, cy + 27*s)], fill=TEAL)
        self.d.line([(cx - 7*s, cy - 2*s), (cx + 11*s, cy - 2*s), (cx + 1*s, cy + 8*s)], fill=NAVY, width=max(2, int(3*s)))

    def line(self, xy, fill=TEAL, width=3):
        self.d.line(xy, fill=fill, width=width, joint='curve')

    def icon(self, cx, cy, kind, color=WHITE, scale=1):
        s = scale
        x, y = cx - 24*s, cy - 24*s
        w = max(2, int(3*s))
        if kind == 'document':
            self.d.rounded_rectangle((x+6*s, y, x+42*s, y+48*s), radius=6*s, outline=color, width=w)
            for offset, length in ((13, 22), (24, 22), (35, 14)):
                self.line((x+13*s,y+offset*s,x+(13+length)*s,y+offset*s), color, w)
        elif kind == 'calendar':
            self.d.rounded_rectangle((x,y+5*s,x+48*s,y+46*s), radius=7*s, outline=color, width=w)
            self.line((x,y+17*s,x+48*s,y+17*s), color, w)
            for px in (12, 35):
                self.line((x+px*s,y,x+px*s,y+11*s), color, w)
            for px, py in ((13,27),(26,27),(13,37),(26,37)):
                self.d.ellipse((x+px*s,y+py*s,x+(px+3)*s,y+(py+3)*s), fill=color)
        elif kind == 'clock':
            self.d.ellipse((x,y,x+48*s,y+48*s), outline=color,width=w)
            self.line((cx,cy-14*s,cx,cy,cx+13*s,cy+8*s),color,w)
        elif kind == 'check':
            self.line((cx-18*s,cy,cx-5*s,cy+13*s,cx+22*s,cy-18*s),color,max(3,int(5*s)))
        elif kind == 'user':
            self.d.ellipse((cx-9*s,cy-24*s,cx+9*s,cy-6*s),outline=color,width=w)
            self.d.arc((cx-23*s,cy-2*s,cx+23*s,cy+40*s),180,360,fill=color,width=w)
            self.line((cx-23*s,cy+19*s,cx+23*s,cy+19*s),color,w)
        elif kind == 'alert':
            self.d.polygon([(cx,cy-26*s),(cx-26*s,cy+22*s),(cx+26*s,cy+22*s)],outline=color,width=w)
            self.line((cx,cy-10*s,cx,cy+6*s),color,w)
            self.d.ellipse((cx-2*s,cy+12*s,cx+2*s,cy+16*s),fill=color)

    def orb(self, cx, cy, radius=78, color=TEAL, icon=None):
        glow = Image.new('RGBA', self.image.size)
        g = ImageDraw.Draw(glow)
        g.ellipse((cx-radius-8,cy-radius-8,cx+radius+8,cy+radius+8),fill=color+'55')
        glow = glow.filter(ImageFilter.GaussianBlur(23))
        self.image = Image.alpha_composite(self.image.convert('RGBA'),glow).convert('RGB')
        self.d = ImageDraw.Draw(self.image)
        self.d.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill='#103B58',outline=color,width=3)
        self.d.ellipse((cx-radius+10,cy-radius+10,cx+radius-10,cy+radius-10),outline='#24607A',width=1)
        if icon:
            self.icon(cx,cy,icon,scale=radius/58)

    def hero_orbit(self):
        cx,cy=902,365
        nodes=((716,221,'document',TEAL),(1012,172,'calendar',BLUE),(1100,486,'clock',TEAL))
        for x,y,kind,color in nodes:
            self.line((cx,cy,x,y),'#245B7C',3)
        self.d.ellipse((cx-155,cy-155,cx+155,cy+155),outline='#123B66',width=2)
        self.d.arc((cx-133,cy-133,cx+133,cy+133),-65,95,fill=BLUE,width=12)
        self.d.arc((cx-133,cy-133,cx+133,cy+133),115,270,fill=TEAL,width=12)
        self.orb(cx,cy,103)
        self.logo(cx,cy,1.7)
        for x,y,kind,color in nodes:
            self.orb(x,y,48,color,kind)

    def chart(self, model, selected=None):
        values=model.get('bars',[0]*7)
        labels=model.get('labels',['Пн','Вт','Ср','Чт','Пт','Сб','Вс'])
        limit=model.get('limit',0)
        maximum=max(max(values,default=0)*1.18,limit*1.25,60)
        x0,y0,w,h=638,230,494,275
        self.d.rounded_rectangle((610,169,1154,594),radius=28,fill='#0B2341',outline='#1C3A5C',width=2)
        for i in range(4):
            y=y0+i*h/3
            self.line((x0,y,x0+w,y),'#183854',1)
        if limit:
            y=y0+h-h*limit/maximum
            for x in range(x0,x0+w,16):
                self.line((x,y,x+8,y),CORAL,2)
            self.text((x0,y-30),f'Порог {limit} мин',20,CORAL)
        for i,value in enumerate(values):
            x=x0+i*71
            bar_h=max(6,h*value/maximum)
            color=CORAL if limit and value>limit else (TEAL if selected is None or selected==i else BLUE)
            self.d.rounded_rectangle((x,y0+h-bar_h,x+45,y0+h),radius=9,fill=color)
            self.d.rounded_rectangle((x-2,y0+h-bar_h-40,x+49,y0+h-bar_h-3),radius=6,fill='#0B2341')
            self.text((x+23,y0+h-bar_h-21),str(value),24,WHITE,700,anchor='mm')
            self.text((x+23,y0+h+33),labels[i],25,TEAL if selected==i else MUTED,700,anchor='mm')
            if selected==i:
                self.d.ellipse((x+18,y0+h+57,x+26,y0+h+65),fill=TEAL)

    def calendar(self, model):
        month=date.fromisoformat(model['month'])
        today=date.fromisoformat(model['today'])
        self.d.rounded_rectangle((619,164,1150,587),radius=28,fill='#0C2544',outline='#245375',width=2)
        self.text((650,185),MONTHS[month.month-1],34,WHITE,800)
        self.text((1118,198),str(month.year),24,MUTED,600,anchor='rm')
        for i,label in enumerate(('Пн','Вт','Ср','Чт','Пт','Сб','Вс')):
            self.text((668+i*69,265),label,22,MUTED,600,anchor='mm')
        for r,week in enumerate(calendar.monthcalendar(month.year,month.month)):
            for col,number in enumerate(week):
                if not number:
                    continue
                day=month.replace(day=number)
                x,y=668+col*69,315+r*45
                selected = day.isoformat()==model.get('selected_due')
                enabled = day.isoformat()>=model.get('earliest',today.isoformat())
                if selected:
                    self.d.rounded_rectangle((x-25,y-21,x+25,y+21),radius=12,fill=BLUE)
                elif day==today and enabled:
                    self.d.rounded_rectangle((x-25,y-21,x+25,y+21),radius=12,fill=TEAL)
                self.text((x,y),number,26,WHITE if selected else (NAVY if day==today and enabled else (WHITE if enabled else '#48627F')),700,anchor='mm')


def render_card(model, directory):
    destination=Path(directory)/f'{model_hash(model)}.png'
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True,exist_ok=True)
    c=Canvas()
    c.logo(80,69,0.75)
    c.text((118,46),'МЕРА',34,WHITE,800)
    c.text((1138,65),'ПЛАНИРОВАНИЕ УЧЕБНОЙ НАГРУЗКИ',18,MUTED,700,anchor='rm')
    c.line((58,112,1142,112),'#1A3B58',1)
    title=model.get('title','ВСЁ\nПО ПЛАНУ')
    lines=title.split('\n')
    kind=model.get('kind','menu')
    title_size=66 if kind in ('preview','warning','done','plan','time') else 74
    while max(c.d.textlength(line,font=font(title_size,800)) for line in lines)>515 and title_size>44:
        title_size-=2
    accent=CORAL if kind=='warning' else TEAL
    for index,line in enumerate(lines):
        c.text((57,170+index*(title_size+5)),line,title_size,WHITE if index==0 else accent,800)
    subtitle_y=180+len(lines)*(title_size+5)+10
    for i,line in enumerate(wrap(c.d,model.get('subtitle','Всё по плану. Всё в меру.'),25,495,max_lines=2)):
        c.text((61,subtitle_y+i*37),line,25,MUTED,500)
    if kind=='load':
        c.chart(model,model.get('selected') if kind=='plan' else None)
        total=sum(model.get('bars',[]))
        c.text((60,440),str(total),79,accent,800)
        c.text((63,537),'минут за неделю',23,MUTED,600)
        if model.get('class_name'):
            c.text((63,587),f'{model["class_name"]} · порог {model.get("limit",0)} мин',22,MUTED,600)
    elif kind=='plan':
        c.chart(model,model.get('selected'))
        c.text((60,402),str(model.get('total',0)),70,TEAL,800)
        caption = 'минут просрочено' if model.get('overdue') else f'из {model.get("capacity",0)} доступных минут'
        if not model.get('overdue') and 'day_spent' in model:
            caption=f'Учтено {model["day_spent"]} · осталось {model["capacity"]} мин'
        c.text((63,487),caption,22,MUTED,600)
        for i,entry in enumerate(model.get('entries',[])[:3]):
            value=f'{entry["subject"]} · {entry["minutes"]} мин'
            c.paragraph((63,530+i*31),value,21,width=490,lines=1,color=WHITE,weight=600)
        if model.get('overflow_minutes'):
            c.text((634,190),f'Не помещается: {model["overflow_minutes"]} мин',20,CORAL,700)
        elif model.get('day'):
            c.text((634,190),f'{model["day"]} · {model.get("count",0)} зад.',20,MUTED,700)
    elif kind=='calendar':
        c.calendar(model)
        c.paragraph((60,421),model.get('subject',''),27,lines=1,color=WHITE,weight=700)
        c.paragraph((60,465),model.get('body',''),23,lines=2)
        selected=model.get('selected_due')
        if selected:
            d=date.fromisoformat(selected)
            c.pill((60,557),f'Выбрано: {d.day}.{d.month:02}',BLUE)
        else:
            c.pill((60,557),'ШАГ 3 ИЗ 4',TEAL)
    elif kind=='wizard':
        step=model.get('step',1)
        values=(model.get('subject') or 'Выберите предмет',model.get('body') or 'Напишите задание',
                model.get('due') or 'Выберите день',f'{model["minutes"]} мин' if model.get('minutes') is not None else 'Укажите минуты')
        for i,label in enumerate(('Предмет','Задание','Срок','Время')):
            y=212+i*100
            active=(i+1)==step
            color=TEAL if i+1<=step else '#375675'
            if i<3:
                c.line((695,y+30,695,y+100),'#22506A',4)
            if active:
                c.d.rounded_rectangle((636,y-35,1140,y+42),radius=22,fill='#113E54',outline=TEAL,width=2)
            c.d.ellipse((666,y-28,724,y+30),fill=color if active else PANEL,outline=color,width=2)
            c.text((695,y+1),str(i+1).zfill(2),25,NAVY if active else color,800,anchor='mm')
            c.text((750,y-28),label.upper(),17,TEAL if active else MUTED,700)
            c.paragraph((750,y-2),values[i],23,width=370,lines=2,color=WHITE if i+1<=step else MUTED,weight=600)
        if model.get('class_name'):
            c.text((62,430),'Класс '+model['class_name'],23,TEAL,700)
        c.paragraph((62,472),model.get('body',''),22,width=490,lines=2)
        c.pill((60,555),f'ШАГ {step} ИЗ 4')
    elif kind in ('preview','warning') and 'total' in model:
        cx,cy,r=889,322,124
        c.d.arc((cx-r,cy-r,cx+r,cy+r),0,360,fill='#193B5B',width=19)
        ratio=min(model['total']/max(1,model['limit']),1)
        c.d.arc((cx-r,cy-r,cx+r,cy+r),-90,-90+max(1,ratio*360),fill=accent,width=19)
        c.text((cx,cy-5),model['total'],78,WHITE,800,anchor='mm')
        c.text((cx,cy+53),f'порог {model["limit"]} мин',24,MUTED,600,anchor='mm')
        c.metric((635,487),'БЫЛО',f'{model.get("before",model["total"]-model.get("minutes",0))} мин',width=232,color=MUTED)
        c.metric((881,487),'ВАШЕ ЗАДАНИЕ',f'+{model.get("minutes",0)} мин',width=232)
        c.pill((635,582),f'+{model["total"]-model["limit"]} МИН СВЕРХ ПОРОГА' if ratio>=1 and kind=='warning' else 'МОЖНО ПУБЛИКОВАТЬ',accent,19)
        c.paragraph((60,402),model.get('subject','Задание'),28,lines=1,color=WHITE,weight=700)
        c.paragraph((60,449),model.get('body',''),24,lines=2)
        c.pill((60,557),model.get('due','Срок не выбран'),TEAL,23)
    elif kind=='manage':
        for a,b in (((895,245),(736,454)),((895,245),(1055,454))):
            c.line((*a,*b),'#2E6382',4)
        for x,y,label,color,role in ((895,237,'Классный руководитель',TEAL,'admin'),(735,450,'Учителя',BLUE,'teacher'),(1056,450,'Ученики',TEAL,'student')):
            c.orb(x,y,59,color,None if 'role_counts' in model else 'user')
            if 'role_counts' in model:
                c.text((x,y),model['role_counts'].get(role,0),48,WHITE,800,anchor='mm')
            c.text((x,y+90),label,23,WHITE,700,anchor='mm')
        if 'role_counts' in model:
            c.metric((60,457),'ПОРОГ НАГРУЗКИ',f'{model.get("limit",0)} мин',width=330)
    elif kind=='done':
        c.orb(897,355,129,TEAL,'check')
        c.d.arc((733,191,1061,519),-70,235,fill=BLUE,width=9)
        c.paragraph((60,408),model.get('subject',''),28,lines=1,color=WHITE,weight=700)
        c.paragraph((60,450),model.get('body',''),22,lines=2)
        if 'minutes' in model:
            c.metric((60,550),'ОЦЕНКА',f'{model["minutes"]} мин')
            actual=model.get('actual')
            c.metric((298,550),'ФАКТИЧЕСКИ',f'{actual} мин' if actual is not None else 'Не указано')
            c.pill((734,552),f'ВЫПОЛНЕНО {model.get("completed",0)} ИЗ {model.get("count",0)}'+(' МИН' if model.get('progress_unit')=='мин' else ''))
        else:
            c.pill((60,535),'МОЖНО ВЫДОХНУТЬ')
    elif kind=='report':
        metrics = model.get('metrics', [])
        for i, (label, value) in enumerate(metrics):
            c.metric((665, 228 + i * 135), label, value, width=445,
                     color=TEAL if i == 0 else MUTED)
        if not metrics:
            c.orb(898, 350, 109, TEAL, 'document')
    elif kind=='tasks':
        c.text((60,437),model.get('count',0),78,TEAL,800)
        c.text((63,529),'заданий в классе',23,MUTED,600)
        c.text((63,579),f'Всего {model.get("total",0)} мин · страница {model.get("page",1)}/{model.get("pages",1)}',21,MUTED,600)
        for i,entry in enumerate(model.get('entries',[])[:3]):
            x,y=620,168+i*145
            c.d.rounded_rectangle((x,y,1146,y+133),radius=23,fill='#0C2745',outline='#28516D')
            c.paragraph((x+19,y+13),entry['subject'],23,width=350,lines=1,color=TEAL,weight=800)
            c.paragraph((x+19,y+48),entry.get('body',''),21,width=355,lines=2)
            c.text((1117,y+39),entry['minutes'],40,WHITE,800,anchor='rm')
            c.text((1117,y+74),'мин',19,MUTED,600,anchor='rm')
            footer=entry.get('due','')
            if entry.get('median') is not None:
                footer+=f' · медиана {entry["median"]:g} мин'
            c.paragraph((x+19,y+105),footer,17,width=480,lines=1)
        if not model.get('entries'):
            c.orb(898,350,109,TEAL,'document')
    elif kind=='help':
        for i,icon in enumerate(('document','clock','check')):
            x,y=711+i*174,325
            if i<2:
                c.line((x+65,y,x+110,y),'#37657D',4)
            c.orb(x,y,60,TEAL if i!=1 else BLUE,icon)
        for i,label in enumerate(('Задание','Время','План')):
            c.text((711+i*174,430),label,24,MUTED,700,anchor='mm')
    elif kind=='time':
        if 'bars' in model:
            c.chart(model,model.get('selected'))
            day=model.get('selected')
            total=model['bars'][day] if day is not None else sum(model['bars'])
            c.text((60,431),total,78,TEAL,800)
            c.text((63,528),'минут в неделю' if day is None else 'минут на '+model['labels'][day],23,MUTED,600)
        else:
            c.orb(900,339,118,TEAL,'clock')
            if 'current_limit' in model:
                c.metric((60,450),'ТЕКУЩИЙ ПОРОГ',f'{model["current_limit"]} мин',width=350)
    elif kind=='warning':
        c.orb(901,355,125,CORAL,'alert')
    else:
        c.hero_orbit()
        if model.get('subject'):
            c.paragraph((60,451),model['subject'],30,width=490,lines=2,color=TEAL,weight=800)
        if 'task_count' in model:
            c.metric((60,442),'ЗАДАНИЙ В КЛАССЕ',model['task_count'])
            c.metric((298,442),'К ЗАВТРА',f'{model.get("tomorrow_minutes",0)} мин')
    if kind not in ('load','plan','preview','warning','calendar','wizard','done','tasks') and model.get('class_name'):
        label=model['class_name']+(' · '+model['role'] if model.get('role') else '')
        c.pill((60,566),label[:32],TEAL,21)
    c.line((58,644,1142,644),'#1A3B58',1)
    c.text((60,670),'УЧИСЬ В СВОЁМ ТЕМПЕ',19,TEAL,700)
    c.text((1140,682),'ЗАДАНИЯ  /  ВРЕМЯ  /  БАЛАНС',17,MUTED,600,anchor='rm')
    c.image.save(destination,optimize=True)
    return destination


if __name__=='__main__':
    examples=[
        {'kind':'menu','title':'ГЛАВНОЕ\nМЕНЮ','subtitle':'Всё по плану. Всё в меру.','class_name':'9А','role':'Учитель','task_count':8,'tomorrow_minutes':160,'limit':180},
        {'kind':'wizard','title':'ДОБАВИТЬ\nЗАДАНИЕ','subtitle':'Оцените время выполнения','step':4,'class_name':'9А','subject':'Алгебра','body':'Страница 42, № 8–10','due':'6 ноября, Пт'},
        {'kind':'calendar','title':'ВЫБЕРИ\nДАТУ','subtitle':'Один тап — и срок выбран','month':'2026-11-01','today':'2026-11-05','selected_due':'2026-11-06','subject':'Алгебра','body':'Страница 42, № 8–10'},
        {'kind':'load','title':'НАГРУЗКА\nКЛАССА','subtitle':'Видно всю неделю, а не один предмет','bars':[75,130,210,90,160,45,0],'labels':['Пн','Вт','Ср','Чт','Пт','Сб','Вс'],'limit':180},
        {'kind':'warning','title':'ВЫСОКАЯ\nНАГРУЗКА','subtitle':'Измените срок или оставьте как есть','total':205,'before':160,'limit':180,'minutes':45,'subject':'История','body':'§ 12, вопросы 1–3. Подготовить краткий пересказ.','due':'6 ноября, Пт'},
        {'kind':'plan','title':'МОЙ\nПЛАН','subtitle':'Освобождаем время для себя','bars':[75,90,60,120,45,30,0],'labels':['Пн','Вт','Ср','Чт','Пт','Сб','Вс'],'selected':0,'total':75,'capacity':120,'count':2,'entries':[{'subject':'Алгебра','minutes':45},{'subject':'История','minutes':30}]},
        {'kind':'manage','title':'УПРАВЛЕНИЕ\nКЛАССОМ','subtitle':'Учителя и ученики — одна команда','class_name':'9А','limit':180,'role_counts':{'admin':1,'teacher':8,'student':25}},
        {'kind':'done','title':'ЗАДАНИЕ\nВЫПОЛНЕНО','subtitle':'Фактическое время и общий прогресс','subject':'Алгебра','body':'Страница 42, № 8–10','minutes':45,'actual':30,'completed':3,'count':8},
        {'kind':'time','title':'УЧЁБА\nВ СВОЁМ ТЕМПЕ','subtitle':'Настройте время под свой день','bars':[60,90,120,60,45,0,0],'labels':['Пн','Вт','Ср','Чт','Пт','Сб','Вс'],'selected':4,'limit':0,'total':375},
        {'kind':'tasks','title':'ЗАДАНИЯ\nКЛАССА','subtitle':'Оценки времени и обратная связь','class_name':'9А','count':3,'total':105,'page':1,'pages':1,'entries':[{'subject':'Алгебра','body':'Страница 42, № 8–10','minutes':45,'due':'6 ноября, Пт','median':30},{'subject':'История','body':'§ 12, вопросы 1–3','minutes':30,'due':'6 ноября, Пт'},{'subject':'Английский язык','body':'Выучить 20 слов, стр. 56','minutes':30,'due':'7 ноября, Сб'}]}
    ]
    out=ROOT/'assets/previews'
    for model in examples:
        p=render_card(model,out)
        target=out/(model['kind']+'.png')
        p.replace(target)
    print(f'Created {len(examples)} example cards (demonstration data).')
