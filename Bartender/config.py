###
# Copyright (c) 2024, Bartender Plugin Contributors
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import supybot.conf as conf
import supybot.registry as registry
from supybot.i18n import PluginInternationalization

_ = PluginInternationalization('Bartender')


def configure(advanced):
    from supybot.questions import yn
    conf.registerPlugin('Bartender', True)


Bartender = conf.registerPlugin('Bartender')

conf.registerChannelValue(
    Bartender, 'enabled',
    registry.Boolean(False, _("""
        Determines whether the Bartender plugin is enabled in this channel.
        When disabled, the bot will tell users the bar is closed.
    """)))

conf.registerChannelValue(
    Bartender, 'cooldown',
    registry.NonNegativeInteger(30, _("""
        Number of seconds the channel must wait between !order commands.
        Set to 0 to disable the cooldown entirely.
    """)))

conf.registerChannelValue(
    Bartender, 'roundCooldown',
    registry.NonNegativeInteger(300, _("""
        Number of seconds the channel must wait between !round commands.
        Set to 0 to disable the cooldown entirely.
    """)))

conf.registerChannelValue(
    Bartender, 'defaultServeMessage',
    registry.String(
        'serves $target a $drink.',
        _("""
        Default serve message when ordering for yourself (or when a drink has
        no custom message). Can be overridden per channel.
        Supports $nick, $target, $drink, $channel.
    """)))

conf.registerChannelValue(
    Bartender, 'defaultServeMessageFor',
    registry.String(
        'serves $target a $drink, courtesy of $nick.',
        _("""
        Default serve message when ordering a drink for someone else
        (target != nick) and the drink has no custom message.
        Can be overridden per channel.
        Supports $nick, $target, $drink, $channel.
    """)))

conf.registerChannelValue(
    Bartender, 'roundMessage',
    registry.String(
        'slides a round of $drink down the bar for everyone in $channel, courtesy of $nick!',
        _("""
        Template for the !round command. Can be overridden per channel.
        Supports $drink, $channel, $nick.
    """)))

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
