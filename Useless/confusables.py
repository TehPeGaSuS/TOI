#!/usr/bin/env python
'''
This script contains a dictionary of keyboard typable characters unicode variants that are very similar.
Other possible variants exist, for now we are only matching the ones that do not "look" like unicode.
This can be used to evade spam filtering by replacing characters with their similar unicode variants.

Todo:
	- Convert unicode characters into raw codepoints in the dictionary (Example: \u202e)
	- Add variants for missing typable characters (iIl)
	- Extend to more variants than already in the database
	- Set different odds on character replacement for chance feature on the confuse function
'''

import random

def confuse(data, chance=False):
	'''chance set to True will give each replacable character a 50% chance of being replaced'''
	confused = str()
	for char in data:
		if char in confusable and (not chance or random.choice((True,False))):
			confused += random.choice(list(confusable[char]))
		else:
			confused += char
	return confused

confusable = {
	' ':'       ',
	'.':'܂․．⡀',
	'!':'ǃⵑ！',
	'$':'＄',
	'%':'％',
	'&':'ꝸ＆',
	':':'ː˸։׃⁚∶ꓽ꞉︰：',
	';':';；',
	'<':'ᐸ＜',
	'=':'⹀꓿＝',
	'>':'ᐳ＞',
	'?':'？',
	'@':'＠',
	'0':'߀𝛰〇𐊒𝟬𝜪Oዐ𝞞𝝤ⵔՕ𝟢𝗢𝘖ⲞОΟଠ𝟎𝐎০୦Ｏ𐊫𝙾ꓳ𐐄𝟶𝑶𝚶𐓂௦౦೦ഠဝ၀ჿᴏᴑⲟ０ｏ𐐬𐓪',
	'1':'𝚕𝟏𝟙𝟣𝟭𝟷',
	'2':'Ƨᒿ２𝟐𝟤𝟮𝟸',
	'3':'ƷȜЗӠⳌꝪꞫ３',
	'4':'Ꮞ𝟒𝟜𝟦𝟰𝟺',
	'5':'Ƽ５',
	'6':'бᏮⳒ６',
	'7':'７𐓒',
	'8':'８𐌚𝟖𝟠𝟪𝟴',
	'9':'৭୨ⳊꝮ９',
	'A':'ΑАᎪᗅᴀꓮꭺＡ𐊠',
	'B':'ΒВᏴᗷꓐＢ𐊂𐊡𝐁𝐵𝑩𝔹𝖡𝗕𝘉𝘽𝚩𝜝𝝗𝞑',
	'C':'СᏟᑕℂⅭ⊂ⲤꓚＣ𐊢𐐕',
	'D':'ᎠᗞᗪⅮꓓꭰＤ𝐃𝐷𝑫𝔻𝖣𝗗𝘋𝘿𝙳',
	'E':'ΕЕᎬ⋿ⴹꓰꭼＥ𐊆',
	'F':'ϜᖴꓝꞘＦ𐊇𐊥𝟋',
	'G':'ɢԌᏀᏳᏻꓖꮐＧ𝐆𝐺𝑮𝔾𝖦𝗚𝘎𝙂𝙶',
	'H':'ʜΗНнᎻᕼⲎꓧＨ𐋏𝐇𝐻𝑯𝖧𝗛𝘏𝙃𝚮𝛨𝜢𝝜𝞖',
	'J':'ͿЈᎫᒍᴊꓙꞲꭻＪ𝐉𝐽𝑱𝕁𝖩𝗝𝙹',
	'K':'ΚКᏦᛕKⲔꓗＫ',
	'L':'ʟᏞᒪⅬⳐⳑꓡꮮＬ𐐛𐑃',
	'M':'ΜϺМᎷᗰᛖⅯⲘꓟＭ𐊰𐌑𝐌𝑀𝑴𝕄𝖬𝗠𝘔𝙈𝙼𝚳𝛭𝜧𝝡𝞛',
	'N':'ɴΝⲚꓠＮ',
	'O':'OΟОՕ௦౦೦ഠဝ၀ჿዐᴏᴑⲞⲟⵔ〇ꓳ０Ｏｏ𐊒𐊫𐐄𐐬𐓂𐓪',
	'P':'ΡРᏢᑭᴘᴩℙⲢꓑꮲＰ𐊕𝐏𝑃𝑷𝖯𝗣𝘗𝙋𝙿𝚸𝛲𝜬𝝦𝞠',
	'Q':'ℚⵕＱ𝐐𝑄𝑸𝖰𝗤𝘘𝙌𝚀',
	'R':'ƦʀᎡᏒᖇᚱꓣꭱꮢＲ𐒴',
	'S':'ЅՏᏕᏚꓢＳ𐊖𐐠',
	'T':'ΤТтᎢᴛ⊤⟙ⲦꓔꭲＴ𐊗𐊱𐌕',
	'U':'Սሀᑌ∪⋃ꓴＵ𐓎',
	'V':'Ѵ٧۷ᏙᐯⅤⴸꓦꛟＶ',
	'W':'ԜᎳᏔꓪＷ',
	'X':'ΧХ᙭ᚷⅩ╳ⲬⵝꓫꞳＸ𐊐𐊴𐌗𐌢',
	'Y':'ΥϒУҮᎩᎽⲨꓬＹ𐊲',
	'Z':'ΖᏃꓜＺ𐋵',
	'a':'ɑαа⍺ａ𝐚𝑎𝒂𝕒𝖆𝖺𝗮𝘢𝙖𝚊𝛂𝛼𝜶𝝰𝞪',
	'b':'ƄЬᏏᑲᖯｂ𝐛𝑏𝒃𝖇𝖻𝗯𝘣𝙗𝚋',
	'c':'ϲсᴄⅽⲥꮯｃ𐐽𝐜𝑐𝒄𝕔𝖈𝖼𝗰𝘤𝙘𝚌',
	'd':'ԁᏧᑯⅆⅾꓒｄ𝐝𝑑𝒅𝒹𝓭𝖽𝗱𝘥𝙙𝚍',
	'e':'еҽ℮ｅ𝐞𝕖𝖾𝗲𝚎',
	'f':'ẝꞙꬵｆ',
	'g':'ƍɡցᶃｇ𝐠𝑔𝒈𝕘𝖌𝗀𝗴𝘨𝙜𝚐',
	'h':'һᏂℎｈ𝒉𝕙𝗁𝗵𝘩𝙝𝚑',
	'j':'ϳјｊ𝐣𝚓',
	'k':'ｋ𝐤𝑘𝒌𝕜𝖐𝗄𝗸𝘬𝙠𝚔',
	'm':'m𝕞𝙢𝗺ⅿ',
	'n':'ոռｎ𝗇𝗻𝘯𝙣𝚗',
	'o':'OΟОՕ௦౦೦ഠဝ၀ჿዐᴏᴑⲞⲟⵔ〇ꓳ０Ｏｏ𐊒𐊫𐐄𐐬𐓂𐓪',
	'p':'ρϱр⍴ⲣｐ𝑝𝕡𝗉𝗽𝘱𝙥𝚙𝛒𝜌𝝆𝞀𝞺',
	'q':'ԛｑ𝐪𝕢𝗊𝗾𝘲𝙦𝚚',
	'r':'гᴦⲅꭈꮁｒ𝐫𝗋𝗿𝚛',
	's':'ѕꜱꮪｓ𐑈',
	't':'ｔ𝘁𝚝',
	'u':'ʋυսᴜꭒｕ𐓶',
	'v':'νѵᴠⅴ∨⋁ꮩｖ',
	'w':'ɯѡԝաᴡꮃｗ',
	'x':'×хⅹ⤫⤬⨯ｘ𝐱𝑥𝒙𝔵𝕩𝖝𝗑𝘅𝘹𝙭𝚡',
	'y':'ɣγуүყỿꭚｙ',
	'z':'ᴢꮓｚ',
	'z':'𝙯ᴢ𝗓𝕫ꮓ𝚣𝒛'
}

for i in range(30):
	print(confuse('facebook'))
