from itertools import groupby
from typing import List, Optional, Tuple

from mev_inspect.schemas.arbitrages import Arbitrage
from mev_inspect.schemas.prices import KLAYTN_STABLE_TOKENS, KLAY_TOKEN_ADDRESSES
from mev_inspect.schemas.swaps import Swap
from mev_inspect.utils import equal_within_percent

MAX_TOKEN_AMOUNT_PERCENT_DIFFERENCE = 0.01


def get_arbitrages(swaps: List[Swap]) -> List[Arbitrage]:
    get_transaction_hash = lambda swap: swap.transaction_hash
    swaps_by_transaction = groupby(
        sorted(swaps, key=get_transaction_hash),
        key=get_transaction_hash,
    )

    all_arbitrages = []

    for _, transaction_swaps in swaps_by_transaction:
        all_arbitrages += _get_arbitrages_from_swaps(
            list(transaction_swaps),
        )

    return all_arbitrages


def _get_arbitrages_from_swaps(swaps: List[Swap]) -> List[Arbitrage]:
    """
    An arbitrage is defined as multiple swaps in a series that result in the initial token being returned
    to the initial sender address.

    There are 2 types of swaps that are most common (99%+).
    Case I (fully routed):
    BOT -> A/B -> B/C -> C/A -> BOT

    Case II (always return to bot):
    BOT -> A/B -> BOT -> B/C -> BOT -> A/C -> BOT

    There is only 1 correct way to route Case I, but for Case II the following valid routes could be found:
    A->B->C->A / B->C->A->B / C->A->B->C. Thus when multiple valid routes are found we filter to the set that
    happen in valid order.
    """
    
    ksp_swaps = [swap for swap in swaps if swap.abi_name == 'KlayswapRouter']
    
    all_arbitrages, used_swaps = _get_arbitrages_from_ksp_swaps(ksp_swaps)

    start_ends = _get_all_start_end_swaps(swaps)
    if len(start_ends) == 0 and len(all_arbitrages) == 0:
        return []

    for (start, ends) in start_ends:
        if start in used_swaps:
            continue

        unused_ends = [end for end in ends if end not in used_swaps]
        
        route = _get_shortest_route(start, unused_ends, swaps)

        if route is not None:
            start_amount = route[0].token_in_amount
            end_amount = route[-1].token_out_amount
            profit_amount = end_amount - start_amount
            error = None
            for swap in route:
                if swap.error is not None:
                    error = swap.error

            arb = Arbitrage(
                swaps=route,
                block_number=route[0].block_number,
                transaction_hash=route[0].transaction_hash,
                account_address=route[0].from_address,
                profit_token_address=route[0].token_in_address,
                start_amount=start_amount,
                end_amount=end_amount,
                profit_amount=profit_amount,
                error=error,
            )
        
            all_arbitrages.append(arb)
            used_swaps.extend(route)

    if len(all_arbitrages) == 1:
        return all_arbitrages
    else:
        return [
            arb
            for arb in all_arbitrages
            if (arb.swaps[0].trace_address < arb.swaps[-1].trace_address)
        ]

def _get_arbitrages_from_ksp_swaps(swaps: List[Swap]):
    arb_ksp: List[Arbitrage] = []
    used_swaps_ksp: List[Swap] = []
    
    if len(swaps) == 0:
        return arb_ksp, used_swaps_ksp
    
    for swap in swaps:
        if (swap.token_in_address == swap.token_out_address and swap.from_address == swap.to_address and swap.token_in_amount < swap.token_out_amount):
            arb_ksp.append(Arbitrage(
                swaps=[swap],
                block_number=swap.block_number,
                transaction_hash=swap.transaction_hash,
                account_address=swap.from_address,
                profit_token_address=swap.token_in_address,
                start_amount=swap.token_in_amount,
                end_amount=swap.token_out_amount,
                profit_amount= swap.token_out_amount - swap.token_in_amount,
                error=swap.error,
            ))
            used_swaps_ksp.append(swap)

    return arb_ksp, used_swaps_ksp  

def _get_shortest_route(
    start_swap: Swap,
    end_swaps: List[Swap],
    all_swaps: List[Swap],
    max_route_length: Optional[int] = None,
) -> Optional[List[Swap]]:
    if len(end_swaps) == 0:
        return None

    if max_route_length is not None and max_route_length < 2:
        return None

    for end_swap in end_swaps:
        if _swap_outs_match_swap_ins(start_swap, end_swap):
            return [start_swap, end_swap]

    if max_route_length is not None and max_route_length == 2:
        return None

    other_swaps = [
        swap for swap in all_swaps if (swap is not start_swap and swap not in end_swaps)
    ]

    if len(other_swaps) == 0:
        return None

    shortest_remaining_route = None
    max_remaining_route_length = (
        None if max_route_length is None else max_route_length - 1
    )

    for next_swap in other_swaps:
        if _swap_outs_match_swap_ins(start_swap, next_swap):
            shortest_from_next = _get_shortest_route(
                next_swap,
                end_swaps,
                other_swaps,
                max_route_length=max_remaining_route_length,
            )

            if shortest_from_next is not None and (
                shortest_remaining_route is None
                or len(shortest_from_next) < len(shortest_remaining_route)
            ):
                shortest_remaining_route = shortest_from_next
                max_remaining_route_length = len(shortest_from_next) - 1

    if shortest_remaining_route is None:
        return None
    else:
        return [start_swap] + shortest_remaining_route


def _get_all_start_end_swaps(swaps: List[Swap]) -> List[Tuple[Swap, List[Swap]]]:
    """
    Gets the set of all possible openings and corresponding closing swaps for an arbitrage via
    - swap[start].token_in == swap[end].token_out
    - swap[start].from_address == swap[end].to_address
    - not swap[start].from_address in all_pool_addresses
    - not swap[end].to_address in all_pool_addresses
    """
    pool_addrs = [swap.contract_address for swap in swaps]
    valid_start_ends: List[Tuple[Swap, List[Swap]]] = []

    for index, potential_start_swap in enumerate(swaps):
        ends_for_start: List[Swap] = []
        remaining_swaps = swaps[:index] + swaps[index + 1 :]

        for potential_end_swap in remaining_swaps:
            is_klay = potential_start_swap.token_in_address in KLAY_TOKEN_ADDRESSES
            if is_klay:
                token_condition = potential_end_swap.token_out_address in KLAY_TOKEN_ADDRESSES
            else:
                token_condition = potential_start_swap.token_in_address == potential_end_swap.token_out_address
            if (
                token_condition
                # and potential_start_swap.from_address == potential_end_swap.to_address
                and not potential_start_swap.from_address in pool_addrs
            ):

                ends_for_start.append(potential_end_swap)

        if len(ends_for_start) > 0:
            valid_start_ends.append((potential_start_swap, ends_for_start))

    return valid_start_ends


def _swap_outs_match_swap_ins(swap_out, swap_in) -> bool:
    is_klay = swap_out.token_out_address in KLAY_TOKEN_ADDRESSES
    if is_klay:
        token_condition = swap_in.token_in_address in KLAY_TOKEN_ADDRESSES
    else:
        token_condition = swap_out.token_out_address == swap_in.token_in_address
    return (
        token_condition
        # We allow the following routing path:
        # BOT_1 -> A/B -> BOT_2 -> BOT_3 -> B/C -> BOT_4 -> C/A -> BOT_5
        # All we need to capture is the `token flows`.
        # and (
        #     swap_out.contract_address == swap_in.from_address
        #     or swap_out.to_address == swap_in.contract_address
        #     or swap_out.to_address == swap_in.from_address
        # )
        and equal_within_percent(
            swap_out.token_out_amount,
            swap_in.token_in_amount,
            MAX_TOKEN_AMOUNT_PERCENT_DIFFERENCE,
        )
    )
