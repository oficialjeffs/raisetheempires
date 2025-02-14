from quest_engine import handle_world_state_change, roll_reward_random_float, handle_quest_progress, \
    progress_upgrades_count, progress_resource_added_count, activate_unlocked_quests, merge_quest_progress
from datetime import datetime
from flask import session
from game_settings import game_settings, lookup_item_by_name, lookup_state_machine, lookup_reference_item, \
    lookup_item_by_code, lookup_visitor_reward
from save_engine import lookup_object, create_backup, lookup_object_save


# TODO add new reference item from clicknext step, use old one for first autostep, new one for 2nd autonext,
#  not needed: is handled by checkstates if new one is null then use old reference item in clicknext(harvesting step?)
# TODO checkState?
def click_next_state(do_click, id, meta, step, reference_item, speed_up=False, tending=False, save=None, playback_tend=False, tend_type=None, cancel=True):
    cur_object = lookup_object(id) if not tending else lookup_object_save(save, id)
    print("cur_object used:", repr(cur_object))
    tend = tending or playback_tend

    game_item = lookup_item_by_name(cur_object['itemName'])
    print("item used:", repr(game_item))

    timestamp = datetime.now().timestamp()
    if 'stateMachineValues' in game_item:
        state_machine = lookup_state_machine(game_item['stateMachineValues']['-stateMachineName'],
                                             game_item['stateMachineValues'].get('define', []),
                                             (lookup_reference_item(cur_object) or {}).get('referenceValues',{}).get('define'))

        print("state_machine used:", repr(state_machine))
        state = lookup_state(state_machine, cur_object.get('state', 0), cur_object, True)
        print("cur state:", repr(state))

        while '-autoNext' in state and state['-stateName'] != state['-autoNext']:   # '-clientDuration': '2.0s', '-duration': '0' respect duration for harvest?
            duration =  parse_duration(state.get('-duration', '0'))
            if cur_object.get('lastUpdated', 0) / 1000 +  duration <= timestamp or speed_up or tend:
                if cur_object.get('lastUpdated', 0) / 1000 + duration > timestamp:
                    speed_up = False  # consumes speed up
                    tend = False
                    print("speed up used")
                next_state_id = state['-autoNext']  # not all states have this!! end states? autostate after time?
                previous_state = state
                state = lookup_state(state_machine, next_state_id, cur_object, True)
                check_state(state_machine, state, cur_object, tending)
                if not tending:
                    do_state_rewards(state, cur_object.get('referenceItem'), meta, playback_tend=playback_tend)
                if 'lastUpdated' not in cur_object:
                    cur_object['lastUpdated'] = 0  #init?
                cur_object['lastUpdated'] += duration * 1000
                cur_object['state'] = next_state_id
                print("pre auto_next_state:", repr(state), 'time', cur_object['lastUpdated'], "duration", duration)
                if not tending:
                    handle_world_state_change(meta, state, state_machine, game_item, step, previous_state, cur_object.get('referenceItem'), cur_object.get('referenceItem'))
            else:
                print("state has autoNext, but not enough time was passed")
                break

        if (do_click or tend):
            try:
               next_state_id = state['-clickNext'] if not cancel else state['-cancelNext']
            except:
                next_state_id = state['-clickNext']
            if reference_item != cur_object.get('referenceItem'):
                state_machine = lookup_state_machine(game_item['stateMachineValues']['-stateMachineName'],
                                                     game_item['stateMachineValues'].get('define', []),
                                                     (lookup_item_by_code(reference_item.split(":")[0]) if reference_item else {})
                                                     .get('referenceValues', {}).get('define'))
            next_click_state = lookup_state(state_machine, next_state_id, cur_object, True)
            check_state(state_machine, next_click_state, cur_object, tending)
            print("next_click_state:", repr(next_click_state))
            if cancel:
                print("canceled state")
            if not tending:
                do_state_rewards(next_click_state, cur_object.get('referenceItem'), meta, playback_tend=playback_tend)
                handle_world_state_change(meta, next_click_state, state_machine, game_item, step, state, reference_item, cur_object.get('referenceItem'))
            else:
                if tend_type == "mine":
                    standard_resources = ["coins", "oil", "wood", "aluminum", "copper", "gold", "iron", "uranium"]
                    tend_type += standard_resources[save['user_object']["userInfo"]["player"]["playerResourceType"]]
                elif tend_type == "harvest":
                    tend_type += "_" + game_item['stateMachineValues']['-referenceType'] if '-referenceType' in game_item['stateMachineValues'] else ""
                    tend_type += "_" + game_item['stateMachineValues']['-referenceSubtype'] if '-referenceSubtype' in game_item['stateMachineValues'] else ""
                elif tend_type == "clear":
                    tend_type += game_item['-subtype']
                #TODO Crew tax

                reward = lookup_visitor_reward(tend_type)
                do_state_rewards(reward, cur_object.get('referenceItem'), meta)

            while '-autoNext' in next_click_state and next_state_id != next_click_state['-autoNext'] and next_click_state.get('-duration', '0') in ['0', '0s']:   #'-clientDuration': '2.0s', '-duration': '0' respect duration for harvest?
                next_state_id = next_click_state['-autoNext']
                previous_state = next_click_state
                next_click_state = lookup_state(state_machine, next_state_id, cur_object, True)
                check_state(state_machine, next_click_state, cur_object, tending)
                print("auto_next_state:", repr(next_click_state))
                if not tending:
                    do_state_rewards(next_click_state, reference_item, meta, playback_tend=playback_tend)
                    handle_world_state_change(meta, next_click_state, state_machine, game_item, step, previous_state, reference_item, reference_item)

            cur_object['state'] = next_state_id
            cur_object['lastUpdated'] = timestamp * 1000
        else:
            print("state has no clicknext, click does nothing" if do_click else "not clicking, only autonexts")
            cur_object['lastUpdated'] = timestamp * 1000
    else:
        print("object has no statemachine, click does nothing")
        cur_object['lastUpdated'] = timestamp * 1000
        if not tending:
            handle_world_state_change(meta, {}, None, game_item, step, {}, reference_item, reference_item)


def lookup_state(state_machine, i, cur_object, check_state):
    [state] = [e for e in state_machine['state'] if e['-stateName'] == str(i)]
    if check_state and 'check_state' in cur_object and str(i) in cur_object['check_state']:
        state = cur_object['check_state'][str(i)]
        print("Overridden state used", str(i))
    return state


#use state from current statemachine as replacement state for that state (with values and 2 random numbers
def check_state(state_machine, state, cur_object, tending):
    if '-checkState' in state:
        check_state = lookup_state(state_machine, state["-checkState"], cur_object, False)
        if 'check_state' not in cur_object:
            cur_object['check_state'] = {}
        cur_object['check_state'][state["-checkState"]] = check_state
        print("Future check state overridden", state["-checkState"])
        if "-xp" in check_state and not tending:
            roll_reward_random_float() # prison xp
        if "-dooberType" in check_state and not tending:
            roll_reward_random_float() # for the platinum pipes


def parse_duration(duration):
    # ["ms", "m", "s", "h", "d"];
    if duration == 'rand:1d,4d':
        return 86400 # 1d
    elif "ms" in duration:
        return float(duration[:-2]) / 1000
    elif "s" in duration:
        return float(duration[:-1])
    elif "m" in duration:
        return float(duration[:-1]) * 60
    elif "h" in duration:
        return float(duration[:-1]) * 3600
    elif "d" in duration:
        return float(duration[:-1]) * 86400
    else:
        return float(duration)


def do_state_rewards(state, reference_item, meta, playback_tend=False):
    player = session['user_object']["userInfo"]["player"]
    player['xp'] += int(state.get('-xp', 0))
    energy = int(state.get('-energy', 0))
    if playback_tend:
        energy = max(energy, 0);
    player['energy'] += energy
    player['cash'] += int(state.get('-cash', 0))
    player['socialXpGood'] += int(state.get('-socialXpGood', 0))
    player['socialXpBad'] += int(state.get('-socialXpBad', 0))

    if  str(state.get('-elementZ',0)) != '0':
        if not state.get('-elementZ',0) in player["inventory"]["items"]:
            player["inventory"]["items"][state.get('-elementZ',0)] = 1
        else:
            player["inventory"]["items"][state.get('-elementZ', 0)] +=1

    world = session['user_object']["userInfo"]["world"]
    resources = world['resources']
    resources['coins'] += int(state.get('-coins', 0))
    resources['energy'] += energy  #which one?
    resources['oil'] += int(state.get('-oil', '0').split('|')[0])
    resources['wood'] += int(state.get('-wood', '0').split('|')[0])

    resource_order = world['resourceOrder']
    resources[resource_order[0]] += int(state.get('-rare', '0').split('|')[0])
    resources[resource_order[0]] += int(state.get('-nrare0', '0').split('|')[0])
    resources[resource_order[1]] += int(state.get('-nrare1', '0').split('|')[0])
    resources[resource_order[2]] += int(state.get('-nrare2', '0').split('|')[0])
    resources[resource_order[3]] += int(state.get('-nrare3', '0').split('|')[0])
    resources[resource_order[4]] += int(state.get('-nrare4', '0').split('|')[0])
    resources["aluminum"] += int(state.get('-aluminum', '0').split('|')[0])
    resources["copper"] += int(state.get('-copper', '0').split('|')[0])
    resources["gold"] += int(state.get('-gold', '0').split('|')[0])
    resources["iron"] += int(state.get('-iron', '0').split('|')[0])
    resources["uranium"] += int(state.get('-uranium', '0').split('|')[0])

    item_inventory = player["inventory"]["items"]
    if int(state.get('-buildable', '0')):
        if reference_item:
            item_inventory[reference_item] = item_inventory.get(reference_item, 0) + 1
            print("Adding", reference_item, "to inventory")
        else:
            print("ERROR: Buildable present but no reference item")

    research = world["research"]
    if int(state.get('-upgrade', '0')):
        if reference_item:
            if reference_item.split(":")[0] not in research.get(reference_item.split(":")[1],[]):
                research[reference_item.split(":")[1]] = research.get(reference_item.split(":")[1],[])  + [reference_item.split(":")[0]]
                print("Adding", reference_item, "to research")
                handle_quest_progress(meta, progress_upgrades_count())
            else:
                print("ERROR: Upgrade already added")
        else:
            print("ERROR: Upgrade present but no reference item")

    level_cash = 0
    levels_count = 0
    levels = [level for level in game_settings['settings']['levels']['level'] if
              int(level["-num"]) > player['level'] and int(level["-requiredXP"]) <= player['xp']]
    for level in levels:
        energy_cap = ([e['-cap'] for e in game_settings['settings']['energycaps']['energycap'] if e['-level'] == level["-num"]] + [46])[0]
        print("Level increased to", level["-num"], "New energy:", energy_cap)
        player['level'] = int(level["-num"])
        player['energy'] = int(energy_cap)
        player['energyMax'] = int(energy_cap)
        levels_count += 1
        if "reward" in level and level["reward"]["-type"] == "cash":
            player['cash'] += int(level["reward"]["-count"])
            level_cash += int(level["reward"]["-count"])
        create_backup("Level " + level["-num"])

    log_rewards = ", ".join(
        [label + " " + ("+" if int(increment) > 0 else "") + str(increment) + " (" + str(total) + ")" for
         (label, increment, total)
         in
         [("xp:", state.get('-xp', '0'), player['xp']),
          ("energy:", str(energy), player['energy']),
          ("coins:", state.get('-coins', '0'), resources['coins']),
          ("oil:", state.get('-oil', '0'), resources['oil']),
          ("wood:", state.get('-wood', '0'), resources['wood']),
          ("cash:", state.get('-cash', '0'), player['cash']),
          ("cash (level):", str(level_cash), player['cash']),
          ("levels:", str(levels_count), player['level']),
          ("socialXpGood:", state.get('-socialXpGood', '0'), player['socialXpGood']),
          ("socialXpBad:", state.get('-socialXpBad', '0'), player['socialXpBad']),
          ("buildable:", state.get('-buildable', '0'), sum(item_inventory.values())),
          (resource_order[0] + ":", state.get('-rare', '0'), resources[resource_order[0]]),
          (resource_order[0] + ":", state.get('-nrare0', '0'), resources[resource_order[0]]),
          (resource_order[1] + ":", state.get('-nrare1', '0'), resources[resource_order[1]]),
          (resource_order[2] + ":", state.get('-nrare2', '0'), resources[resource_order[2]]),
          (resource_order[3] + ":", state.get('-nrare3', '0'), resources[resource_order[3]]),
          (resource_order[4] + ":", state.get('-nrare4', '0'), resources[resource_order[4]]),
          ("aluminum" + ":", state.get('-aluminum', '0'), resources["aluminum"]),
          ("copper" + ":", state.get('-copper', '0'), resources["copper"]),
          ("gold" + ":", state.get('-gold', '0'), resources["gold"]),
          ("iron" + ":", state.get('-iron', '0'), resources["iron"]),
          ("uranium" + ":", state.get('-uranium', '0'), resources["uranium"])
          ] if int(increment.split('|')[0]) != 0])
    if log_rewards:
        print("State rewards:", log_rewards)
    handle_quest_progress(meta, progress_resource_added_count(state, "-"))
    if levels:
        new_quests = []
        if "QuestComponent" not in meta:
            meta['QuestComponent'] = []
        activate_unlocked_quests(new_quests, meta)
        merge_quest_progress(new_quests, meta['QuestComponent'], "output quest")
        merge_quest_progress(new_quests, session['quests'], "session quest")

        
        
def do_costs(costs):
    player = session['user_object']["userInfo"]["player"]
    player['xp'] -= int(costs.get('-xp', 0))
    player['energy'] -= int(costs.get('-energy', 0))
    player['cash'] -= int(costs.get('-cash', 0))
    player['socialXpGood'] -= int(costs.get('-socialXpGood', 0))
    player['socialXpBad'] -= int(costs.get('-socialXpBad', 0))

    world = session['user_object']["userInfo"]["world"]
    resources = world['resources']
    resources['coins'] -= int(costs.get('-coins', 0))
    resources['energy'] -= int(costs.get('-energy', 0)) #which one?
    resources['oil'] -= int(costs.get('-oil', '0').split('|')[0])
    resources['wood'] -= int(costs.get('-wood', '0').split('|')[0])

    resource_order = world['resourceOrder']
    resources[resource_order[0]] -= int(costs.get('-rare', '0').split('|')[0])
    resources[resource_order[0]] -= int(costs.get('-nrare0', '0').split('|')[0])
    resources[resource_order[1]] -= int(costs.get('-nrare1', '0').split('|')[0])
    resources[resource_order[2]] -= int(costs.get('-nrare2', '0').split('|')[0])
    resources[resource_order[3]] -= int(costs.get('-nrare3', '0').split('|')[0])
    resources[resource_order[4]] -= int(costs.get('-nrare4', '0').split('|')[0])

    log_costs = ", ".join([label + " " + ("+" if int(decrement) < 0 else "") + str(-int(decrement)) + " (" + str(total) + ")" for
                     (label, decrement, total)
                     in
                     [("xp:", costs.get('-xp', '0'), player['xp']),
                      ("energy:", costs.get('-energy', '0'), player['energy']),
                      ("coins:", costs.get('-coins', '0'), resources['coins']),
                      ("oil:", costs.get('-oil', '0'), resources['oil']),
                      ("wood:", costs.get('-wood', '0'), resources['wood']),
                      ("cash:", costs.get('-cash', '0'), player['cash']),
                      ("socialXpGood:", costs.get('-socialXpGood', '0'), player['socialXpGood']),
                      ("socialXpBad:", costs.get('-socialXpBad', '0'), player['socialXpBad']),
                      (resource_order[0] + ":", costs.get('-rare', '0'), resources[resource_order[0]]),
                      (resource_order[0] + ":", costs.get('-nrare0', '0'), resources[resource_order[0]]),
                      (resource_order[1] + ":", costs.get('-nrare1', '0'), resources[resource_order[1]]),
                      (resource_order[2] + ":", costs.get('-nrare2', '0'), resources[resource_order[2]]),
                      (resource_order[3] + ":", costs.get('-nrare3', '0'), resources[resource_order[3]]),
                      (resource_order[4] + ":", costs.get('-nrare4', '0'), resources[resource_order[4]])
                      ] if int(decrement.split('|')[0]) != 0])
    if log_costs:
        print("Costs:", log_costs)