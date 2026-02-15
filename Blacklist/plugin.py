### plugin.py
# Copyright (c) 2022, Mike Oxlong
# V1.07 - Added ignoredBanMasks to skip tracking bans from services
###

import json, os, time, threading, re
import urllib.request, urllib.parse

from supybot.commands import *
from supybot import callbacks, conf, ircmsgs, ircutils, schedule

try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('Blacklist')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x

class Blacklist(callbacks.Plugin):
    """A custom ban tracking plugin to keep a channel's banlist cleaner"""
    
    banmasks = {0: '*!ident@host',
                1: '*!*ident@host',
                2: '*!*@host',
                3: '*!*ident@*.phost',
                4: '*!*@*.phost',
                5: 'nick!ident@host',
                6: 'nick!*ident@host',
                7: 'nick!*@host',
                8: 'nick!*ident@*.phost',
                9: 'nick!*@*.phost',
                10: '*!ident@*'}
    
    threaded = True
    def __init__(self, irc):
        self.__parent = super(Blacklist, self)
        self.__parent.__init__(irc)
        self.dbfile = os.path.join(str(conf.supybot.directories.data), 'Blacklist', 'blacklist.json')
        self.db = {}
        self._initdb()
    
    def _initdb(self):
        try:
            with open(self.dbfile, 'r') as f: self.db = json.load(f)
        except IOError:
            self._dbWrite()
    
    def _write(self, lock):
        if not os.path.exists(os.path.dirname(self.dbfile)):
            os.mkdir(os.path.dirname(self.dbfile))
        with lock, open(self.dbfile, 'w') as f: json.dump(self.db, f)
    
    def _dbWrite(self):
        lock = threading.Lock()
        threading.Thread(target=self._write,args=(lock,)).start()
    
    def _elapsed(self, inp):
        lapsed = int(time.time()-inp)
        L = (1, 60, 3600, 86400, 604800, 2592000, 31536000)
        T = ('s', 'm', 'h', 'd', 'w', 'mo')
        for l, t in zip(L, T):
            if lapsed < L[L.index(l)+1]:
                return f'{int(lapsed/l)}{t}'
        return f'{int(lapsed/60/60/24/365)}y'
    
    def _createMask(self, irc, target, num):
        nick, ident, host = ircutils.splitHostmask(irc.state.nickToHostmask(target))
        mask = re.sub(
            "(nick|ident|host|phost)",
            lambda match: {
                "nick": nick,
                "ident": ident,
                "host": host,
                "phost": host.split(".")[1]
                if "." in host
                else mask.split("@")[0] + f"@{host}",
            }[match.group(1)],
            self.banmasks[num],
        )
        return mask
    
    def _sendToPaste(self, content):
        """Send content to paste.debian.net and return URL"""
        try:
            data = urllib.parse.urlencode({
                'content': content,
                'lang': 'text',
                'submit': 'submit'
            }).encode('utf-8')
            
            req = urllib.request.Request('https://paste.debian.net/', data=data)
            with urllib.request.urlopen(req, timeout=10) as response:
                # The paste service redirects to the paste URL
                return response.url
        except Exception as e:
            self.log.error(f'Failed to send to paste service: {e}')
            return None
    
    def doMode(self, irc, msg):
        if msg.args[1:] and msg.args[1] == '+b' and \
          not ircutils.hostmaskPatternEqual(msg.prefix, irc.prefix) and \
          self.registryValue('addManualBans', msg.args[0]) and \
          irc.state.channels[msg.args[0]].isHalfopPlus(irc.nick) and \
          not ircutils.strEqual(msg.nick, irc.nick) and \
          (msg.args[0] not in self.db or msg.args[2] not in self.db[msg.args[0]]):
            channel = msg.args[0]
            mask = msg.args[2]
            
            # STOP! Don't track extbans (anything starting with ~)
            if mask.startswith('~'):
                self.log.info(f'Ignoring extban in {channel}: {mask}')
                return
            
            # STOP! Don't track bans from ignored hostmasks (like services)
            ignored_masks = self.registryValue('ignoredBanMasks', channel)
            for ignored_pattern in ignored_masks:
                if ircutils.hostmaskPatternEqual(ignored_pattern, msg.prefix):
                    self.log.info(f'Ignoring ban from {msg.prefix} (matches {ignored_pattern}) in {channel}')
                    return
            
            # Calculate expiry for manually added bans (use banlistExpiry setting)
            expiry_time = int(time.time()) + (self.registryValue('banlistExpiry', channel) * 60)
            try: self.db[channel][mask] = [msg.nick, time.time(), '*user-added ban', expiry_time, False]
            except KeyError: self.db[channel] = {mask: [msg.nick, time.time(), '*user-added ban', expiry_time, False]}
            self._dbWrite()
            irc.reply(f'"{mask}" added to the banlist for {channel}.')
            
            # Schedule automatic removal from channel (keep in database like normal bans)
            def _normalExpiry():
                # Only remove from channel, keep in database
                irc.queueMsg(ircmsgs.unban(channel, mask))
            
            schedule.addEvent(_normalExpiry,
                              time.time()+(self.registryValue('banlistExpiry', channel)*60),
                              f'bl_unban_{channel}{mask}')
    
    def doJoin(self, irc, msg):
        if self.registryValue('enabled', msg.args[0]) and \
          irc.state.channels[msg.args[0]].isHalfopPlus(irc.nick) and \
          not ircutils.strEqual(msg.nick, irc.nick) and msg.args[0] in self.db:
            for mask in self.db[msg.args[0]]:
                if ircutils.hostmaskPatternEqual(mask, msg.prefix):
                    irc.queueMsg(ircmsgs.ban(msg.args[0], mask))
                    irc.queueMsg(ircmsgs.kick(msg.args[0], msg.nick, self.db[msg.args[0]][mask][2]))
                    schedule.addEvent(lambda: irc.queueMsg(ircmsgs.unban(msg.args[0], mask)),
                                      time.time()+(self.registryValue('banlistExpiry', msg.args[0])*60),
                                      f'bl_unban_{msg.args[0]}{mask}')
                    break
    
    def add(self, irc, msg, args, channel, target, reason):
        """[<channel>] <nick|mask> [<reason>]
        
        Add <nick|hostmask> to blacklist database (requires #channel,op capability)"""
        self._ban(irc, msg, args, channel, target, None, reason)
    add = wrap(add, [('checkChannelCapability', 'op'), 'channel',
                     'somethingWithoutSpaces', optional('text')])
    
    def timer(self, irc, msg, args, channel, target, timer, reason):
        """[<channel>] <nick|mask> [<expiry>] [<reason>]
        
        Add <nick|hostmask> to blacklist database, expiry is given in minutes (requires #channel,op capability)"""
        if not timer: timer = self.registryValue('banTimerExpiry', channel)
        self._ban(irc, msg, args, channel, target, timer, reason)
    timer = wrap(timer, [('checkChannelCapability', 'op'), 'channel',
                         'somethingWithoutSpaces', optional('PositiveInt'),
                         optional('text')])
    
    def _ban(self, irc, msg, args, channel, target, timer, reason):
        if not self.registryValue('enabled', channel):
            irc.error(f'Database is disabled in {channel}.')
            return
        if not irc.state.channels[channel].isHalfopPlus(irc.nick):
            irc.error(f'I have no powers in {channel}.')
            return
        if channel not in irc.state.channels:
            irc.error(f'I\'m not in {channel}.')
            return
        if ircutils.isUserHostmask(target):
            if ircutils.hostmaskPatternEqual(target, irc.prefix):
                irc.error('You want me to blacklist myself?!')
                return
            # STOP! Don't allow extbans
            if target.startswith('~'):
                irc.error('Extbans are not supported. Use traditional hostmasks only.')
                return
            mask = target
        elif irc.isNick(target):
            if ircutils.strEqual(target, irc.nick):
                irc.error('You want me to blacklist myself?!')
                return
            if target not in irc.state.channels[channel].users:
                irc.error(f'"{target}" is not in {channel}.')
                return
            mask = self._createMask(irc, target, self.registryValue('maskNumber', channel))
        else:
            irc.error(f'Invalid nick or banmask.')
            return
        if mask in irc.state.channels[channel].bans:
            irc.error(f'"{mask}" is already in banlist for {channel}.')
            return
        if not reason:
            reason = self.registryValue('banReason', channel)
        # Calculate expiry timestamp
        if timer:
            expiry_time = int(time.time()) + (timer * 60)
        else:
            expiry_time = int(time.time()) + (self.registryValue('banlistExpiry', channel) * 60)
        
        if channel not in self.db or mask not in self.db[channel]:
            # Store: [banner_nick, ban_timestamp, reason, expiry_timestamp, is_timed]
            try: self.db[channel][mask] = [msg.nick, int(time.time()), reason, expiry_time, bool(timer)]
            except KeyError: self.db[channel] = {mask: [msg.nick, int(time.time()), reason, expiry_time, bool(timer)]}
            self._dbWrite()
            irc.reply(f'"{mask}" added to the banlist for {channel}.')
        irc.queueMsg(ircmsgs.ban(channel, mask))
        for nick in irc.state.channels[channel].users:
            if ircutils.hostmaskPatternEqual(mask, irc.state.nickToHostmask(nick)):
                irc.queueMsg(ircmsgs.kick(channel, nick, reason))
        
        if timer:
            # TIMED BAN: Remove from channel AND database when expired
            def _timedExpiry():
                # Remove from database
                if channel in self.db and mask in self.db[channel]:
                    del self.db[channel][mask]
                    if len(self.db[channel]) == 0:
                        del self.db[channel]
                    self._dbWrite()
                # Remove from channel
                irc.queueMsg(ircmsgs.unban(channel, mask))
            
            schedule.addEvent(_timedExpiry,
                              time.time()+(timer*60), 
                              f'bl_timed_unban_{channel}{mask}')
        else:
            # NORMAL BAN: Remove from channel but keep in database
            def _normalExpiry():
                # Only remove from channel, keep in database
                irc.queueMsg(ircmsgs.unban(channel, mask))
            
            schedule.addEvent(_normalExpiry,
                              time.time()+(self.registryValue('banlistExpiry', channel)*60),
                              f'bl_unban_{channel}{mask}')
    
    def remove(self, irc, msg, args, channel, mask):
        """[<channel>] <mask>
        
        Remove a mask from the blacklist database (requires #channel,op capability)"""
        if channel not in irc.state.channels:
            irc.error(f'I\'m not in {channel}.')
            return
        if channel not in self.db or mask not in self.db[channel]:
            irc.error(f'"{mask}" is not in my banlist for {channel}.')
            return
        try:
            schedule.removeEvent(f'bl_unban_{channel}{mask}')
            schedule.removeEvent(f'bl_timed_unban_{channel}{mask}')
        except: pass
        if mask in irc.state.channels[channel].bans:
            irc.queueMsg(ircmsgs.unban(channel, mask))
        del self.db[channel][mask]
        if len(self.db[channel]) == 0:
            del self.db[channel]
        self._dbWrite()
        irc.reply(f'"{mask}" removed from the banlist in {channel}.')
    remove = wrap(remove, [('checkChannelCapability', 'op'), 'channel', 'text'])
    
    def list(self, irc, msg, args, channel):
        """[<channel>]
        
        Returns a list of banmasks stored in <channel> (requires #channel,op capability)"""
        if channel not in self.db:
            irc.reply(f'The banlist for {channel} is currently empty.')
            return
        
        # Get the max entries to display directly in channel
        max_output = self.registryValue('maxListOutput', channel)
        
        # Build the output
        lines = []
        lines.append(f'Banlist for {channel} ({len(self.db[channel])} entries)')
        lines.append('=' * 80)
        
        padwidth = len(max((mask for mask in self.db[channel]), key=len))
        for banmask, v in self.db[channel].items():
            elapsed = self._elapsed(v[1])
            
            # Handle both old format [nick, timestamp, reason] and new format [nick, timestamp, reason, expiry, is_timed]
            if len(v) >= 4:
                # New format with expiry timestamp
                expiry_timestamp = v[3]
                is_timed = v[4] if len(v) >= 5 else False
                expiry_str = time.strftime('%d/%m/%Y - %H:%M:%S', time.localtime(expiry_timestamp))
                ban_type = "(timed)" if is_timed else "(permanent)"
                lines.append(f'{banmask.ljust(padwidth, " ")} - Added by {v[0]} {elapsed} ago - Expires: {expiry_str} {ban_type} - Reason: {v[2]}')
            else:
                # Old format without expiry (backward compatibility)
                lines.append(f'{banmask.ljust(padwidth, " ")} - Added by {v[0]} {elapsed} ago (reason: {v[2]})')
        
        content = '\n'.join(lines)
        
        # If maxListOutput is 0 or list exceeds threshold, send to paste service
        if max_output == 0 or len(self.db[channel]) > max_output:
            paste_url = self._sendToPaste(content)
            if paste_url:
                irc.reply(f'Banlist for {channel} ({len(self.db[channel])} entries): {paste_url}')
            else:
                irc.error('Failed to upload banlist to paste service. Check logs for details.')
        else:
            # Send directly to channel
            for line in lines[2:]:  # Skip header lines for direct output
                irc.reply(line)
    
    list = wrap(list, [('checkChannelCapability', 'op'), 'channel'])

Class = Blacklist

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
