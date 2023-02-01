# Changelog

<!--next-version-placeholder-->

## v1.3.0 (2023-02-01)
### Feature
* Use locks for throttled requests ([`b2db6a7`](https://github.com/guilatrova/gracy/commit/b2db6a760b097b27142f17bf533d760e4e99605c))

### Fix
* Throttling/allowed not working ([`cb0251b`](https://github.com/guilatrova/gracy/commit/cb0251b49c43f9376783e6f457073410f6d326a1))

## v1.2.1 (2023-02-01)
### Fix
* Handle scenarios for just 1 request per url ([`f4f799b`](https://github.com/guilatrova/gracy/commit/f4f799bbc03ae318fba69dd299fb423800a18651))

## v1.2.0 (2023-02-01)
### Feature
* Simplify req/s rate to the user ([`1b428c7`](https://github.com/guilatrova/gracy/commit/1b428c788f192e0e23c49b27d9a46438d20d230a))
* Include req rate in report ([`e387a25`](https://github.com/guilatrova/gracy/commit/e387a25f831a27f031ebc1625ac642beb3895678))
* Clear base urls with ending slash ([`51fb8ee`](https://github.com/guilatrova/gracy/commit/51fb8ee9e369eecd951fb31da92edc3317e63483))
* Implement retry logging ([`f2d3238`](https://github.com/guilatrova/gracy/commit/f2d3238830bbda163b8b55f874f2ae7ecb11d6df))

### Fix
* Consider retry is unset ([`0ca1ed9`](https://github.com/guilatrova/gracy/commit/0ca1ed9e65faa8e1e7efd024a7264dbc328a3259))
* Retry must start with 1 ([`3e3e750`](https://github.com/guilatrova/gracy/commit/3e3e75003092bca7f4181c17b68a873ec77c31d1))

### Documentation
* Fix download badge ([`22a9d7a`](https://github.com/guilatrova/gracy/commit/22a9d7a132b86c6da084b6f59ddba74f64814238))
* Improve examples ([`4ca1f7d`](https://github.com/guilatrova/gracy/commit/4ca1f7df80b6b1bba9f255983a6be5b906b09a85))
* Add new placeholders ([`8eba619`](https://github.com/guilatrova/gracy/commit/8eba619dd73544861960b0a9a381fe97d2c5468f))
* Add some notes for custom exceptions ([`225f008`](https://github.com/guilatrova/gracy/commit/225f00828697d8a611bb596e1f3119570a1b363e))

## v1.1.0 (2023-01-30)
### Feature
* Change api to be public ([`3b0c828`](https://github.com/guilatrova/gracy/commit/3b0c8281c3e164d9a7f01770c698fa825afe562a))

### Documentation
* Fix examples/info ([`0193f11`](https://github.com/guilatrova/gracy/commit/0193f112807f4621f5fd35acc9fbec32c4a2554c))

## v1.0.0 (2023-01-30)
### Feature
* Drop python 3.7 support ([`0f69e5b`](https://github.com/guilatrova/gracy/commit/0f69e5be00f8202ea2aa98b71630ae167c6431f1))

### Breaking
* drop python 3.7 support ([`0f69e5b`](https://github.com/guilatrova/gracy/commit/0f69e5be00f8202ea2aa98b71630ae167c6431f1))

### Documentation
* Add remaining sections ([`4335b5a`](https://github.com/guilatrova/gracy/commit/4335b5a3313a56c36b7b54c9ec44a07b2e6b4bd0))
* Add throttling ([`6fc9583`](https://github.com/guilatrova/gracy/commit/6fc958328fcbc5304e745c29918f8ffb2f8fa1a4))
* Add retry ([`aa8a828`](https://github.com/guilatrova/gracy/commit/aa8a82844a8c77f99897512d23b01eb216b8e0ff))
* Add credits/settings section ([`113bf48`](https://github.com/guilatrova/gracy/commit/113bf4886ae50418ddaef62d6f4880171f98240f))
* Write about parsing ([`c133cda`](https://github.com/guilatrova/gracy/commit/c133cda6444058861a5129db5da0a4fd7a12965e))
* Remove colspans ([`3ef5fd7`](https://github.com/guilatrova/gracy/commit/3ef5fd77dbec659144a034405c815fa5a060d747))
* Add logging details ([`09e923c`](https://github.com/guilatrova/gracy/commit/09e923cb9bb14b858f8c6ab975fb50ffab8fd42a))
* Fix badge ([`fea301a`](https://github.com/guilatrova/gracy/commit/fea301a63db98398101ae796f3a14f35882922f7))
* Add empty topics ([`887b46c`](https://github.com/guilatrova/gracy/commit/887b46ca3a61d20fcc942e18868a159ffaded0f1))
* Improve top description ([`e745403`](https://github.com/guilatrova/gracy/commit/e745403116483c651ebcd9f7e26fe99ab468ad03))

## v0.6.0 (2023-01-29)
### Feature
* Implement throttling ([`8691045`](https://github.com/guilatrova/gracy/commit/869104595b7c6954ea31b159e89a1efe8028215c))

### Fix
* **throttling:** Resolve bugs ([`4c41326`](https://github.com/guilatrova/gracy/commit/4c4132608b61256b8949dcbc46558641bccceedf))
* **throttling:** Handle some scenarios ([`f9d4fbc`](https://github.com/guilatrova/gracy/commit/f9d4fbc5c2e6e378cdfdd7dc8a930852f9620477))

### Documentation
* Improve prop description ([`27f9e01`](https://github.com/guilatrova/gracy/commit/27f9e01dd5004827a8df471034138ad1bf18b10c))

## v0.5.0 (2023-01-29)
### Feature
* Implement custom exceptions ([`2d89ebd`](https://github.com/guilatrova/gracy/commit/2d89ebd4c862c60bfc816774c3102c8e9e43ed2a))
* Implement retry pass ([`45e8ce6`](https://github.com/guilatrova/gracy/commit/45e8ce6124127ef69f5a9704a6ae0dc4a48d1f45))

## v0.4.0 (2023-01-29)
### Feature
* Implement parser ([`ab48cd9`](https://github.com/guilatrova/gracy/commit/ab48cd937cfa37e4455260defa94a8d41620f878))

### Documentation
* Add custom logo ([`19f6bf8`](https://github.com/guilatrova/gracy/commit/19f6bf86b4daf68ee50908cf2833912b0f3de852))

## v0.3.0 (2023-01-29)
### Feature
* Improve client customization ([`1372b4f`](https://github.com/guilatrova/gracy/commit/1372b4fb9ba7fc6d2c9b8f5e3064f4e2c9fd9ab5))

## v0.2.0 (2023-01-28)
### Feature
* Calculate footer totals ([`eb77c71`](https://github.com/guilatrova/gracy/commit/eb77c7138c50511cb1d4edfbd7c6f77b52ca6989))
* Sort table by requests made desc ([`fced5eb`](https://github.com/guilatrova/gracy/commit/fced5eb47dcc87abc97f2d91b1905140ad4d65d9))
* Add custom color to status ([`9964723`](https://github.com/guilatrova/gracy/commit/99647237155aa0f8b6d236ffbaa71d6d616c4ea7))
* Fold correct column ([`4a0bff0`](https://github.com/guilatrova/gracy/commit/4a0bff08a67e3c549897cac3ce9ec6d94603c2e7))

## v0.1.0 (2023-01-28)
### Feature
* Fold url column ([`a4b0ed0`](https://github.com/guilatrova/gracy/commit/a4b0ed0c1b2fe2b313c113e9ecdb6020b2f949a4))
* Display status range in metrics ([`8d01476`](https://github.com/guilatrova/gracy/commit/8d0147613c83c708064d05033ba1e7a24d3fa6cf))
* Add custom color to failed requests ([`65c9ab7`](https://github.com/guilatrova/gracy/commit/65c9ab7c2db0f90b1a3f48c4ab74eb2d3a96dd42))
* Add rich table to display metrics ([`44944f7`](https://github.com/guilatrova/gracy/commit/44944f7874f474fc33b4f532260a65720df0c051))
* Implement logs ([`9caee55`](https://github.com/guilatrova/gracy/commit/9caee5576f9d8cf3f9a17429b54e5dd26df9fb15))
* Add stub for report ([`b394afe`](https://github.com/guilatrova/gracy/commit/b394afe66a5fadb3c4831f2ceb75842b717465b4))
* Narrow down retry logic ([`e444281`](https://github.com/guilatrova/gracy/commit/e444281be9f0e8d9752e0ae847a768fddd1c1586))
* Make gracy async ([`5edacca`](https://github.com/guilatrova/gracy/commit/5edacca8781c02b7046a636020a7847faf716e8e))
* Implement retry ([`f0a794a`](https://github.com/guilatrova/gracy/commit/f0a794a40a6d351b02b516fa3a0004798a0710c2))
* Implement strict/allowed status code ([`171688b`](https://github.com/guilatrova/gracy/commit/171688b591c0c88b825f5ff1590f55c5cf0e1a9d))

### Fix
* Use enum value for _str_ ([`345464f`](https://github.com/guilatrova/gracy/commit/345464f44a48d864d5a39e56dfadf94f6f55da16))

### Documentation
* Reword some stuff ([`546f3fc`](https://github.com/guilatrova/gracy/commit/546f3fc6188c312196c9ca69a5fb80e172b6738f))
* Slightly improve readme ([`8a56b3d`](https://github.com/guilatrova/gracy/commit/8a56b3d961cf3ad343d7c95412ce49184e914608))
* Fill with some gracy stuff ([`8183d26`](https://github.com/guilatrova/gracy/commit/8183d2686f8f3a4cdfc50bf8e13465edfc54ef6d))
