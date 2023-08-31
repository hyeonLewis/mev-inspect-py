from typing import List, Optional

from mev_inspect.classifiers.helpers import create_swap_from_pool_transfers_for_ksp
from mev_inspect.schemas.classifiers import ClassifierSpec, SwapClassifier
from mev_inspect.schemas.swaps import Swap
from mev_inspect.schemas.traces import DecodedCallTrace, Protocol
from mev_inspect.schemas.transfers import Transfer

KLAYSWAP_PAIR_ABI_NAME = "KlayswapRouter"

class KlayswapSwapClassifier(SwapClassifier):
    @staticmethod
    def parse_swap(
        trace: DecodedCallTrace,
        prior_transfers: List[Transfer],
        child_transfers: List[Transfer],
        length: int
    ) -> Optional[Swap]:

        recipient_address = trace.inputs.get("to", trace.from_address)
    
        swap = create_swap_from_pool_transfers_for_ksp(
            trace, recipient_address, prior_transfers, child_transfers, length
        )
    
        return swap

# KLAYSWAP_CONTRACT_SPECS = [
#     ClassifierSpec(
#         abi_name="KlayswapRouter",
#         protocol=Protocol.klayswap,
#         valid_contract_addresses=["0xc6a2ad8cc6e4a7e08fc37cc5954be07d499e7654"],
#     ),
# ]

KLAYSWAP_PAIR_SPEC = ClassifierSpec(
    abi_name=KLAYSWAP_PAIR_ABI_NAME,
    protocol=Protocol.klayswap,
    classifiers={
        "exchangeKlayPos(address,uint256,address[])": KlayswapSwapClassifier,
        "exchangeKctPos(address,uint256,address,uint256,address[])": KlayswapSwapClassifier,
        "exchangeKlayNeg(address,uint256,address[])": KlayswapSwapClassifier,
        "exchangeKctNeg(address,uint256,address,uint256,address[])": KlayswapSwapClassifier,
    },
)

KLAYSWAP_CLASSIFIER_SPECS: List = [
    #*KLAYSWAP_CONTRACT_SPECS,
    KLAYSWAP_PAIR_SPEC,
]
