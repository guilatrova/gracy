# Changelog

<!--next-version-placeholder-->

## v1.34.0 (2024-11-27)

### Feature

* Garbage collector improvements ([`36e537e`](https://github.com/guilatrova/gracy/commit/36e537ec85fd12c273a74945cf7962cbdd7494bd))

## v1.33.1 (2024-10-07)

### Fix

* Don't stop if ctx token reset fails ([`4ddad79`](https://github.com/guilatrova/gracy/commit/4ddad79627a5fd528a199d8bf6e58906d75cbec1))

## v1.33.0 (2024-02-20)

### Feature

* Enhance response typing ([`95f8c16`](https://github.com/guilatrova/gracy/commit/95f8c162de7ab64a925bf008e81e11b138426320))

### Fix

* Add typing_extensions to support deprecated ([`f6b2b7b`](https://github.com/guilatrova/gracy/commit/f6b2b7b1baf7a39c5f6f21cced7aeeeac23cbfe7))

### Documentation

* Update on dynamic type hinting ([`6d7975d`](https://github.com/guilatrova/gracy/commit/6d7975d1b383d040f2c960d1dc13482530b81571))

## v1.32.2 (2024-01-31)
### Fix

* Define correct typing ([`7c889fe`](https://github.com/guilatrova/gracy/commit/7c889fe782e7705843d84ec6dc1b3f0098e779e0))

## v1.32.1 (2024-01-15)
### Fix

* **retry:** Verify original exc for failed requests ([`31e79d7`](https://github.com/guilatrova/gracy/commit/31e79d75e6bbeedfdbbc7f69f2c254017c21e22c))
* Format original exception name properly ([`56254b6`](https://github.com/guilatrova/gracy/commit/56254b6e99325b550d3a33512918ab6bb0a56b9a))

## v1.32.0 (2023-12-09)
### Feature

* Improve concurrency logging ([`5292faf`](https://github.com/guilatrova/gracy/commit/5292faf9eff54e9290ab2db836677f44c15b4673))
* Add plotly printer ([`5b8a460`](https://github.com/guilatrova/gracy/commit/5b8a460ffeb8a363b2dfcbef95b3b6134c1f205a))
* Track requests timeline ([`9c30ba2`](https://github.com/guilatrova/gracy/commit/9c30ba2d84a9485d44530df9b82a7bcaf04cd01e))
* Replace manual impl with semaphore ([`57df089`](https://github.com/guilatrova/gracy/commit/57df08936d9f4a808cddd3857058f0f23ba3350b))

### Documentation

* Add explanation ([`b1ea801`](https://github.com/guilatrova/gracy/commit/b1ea80122d6915a207e95780928918c3253d532a))
* Explain concurrent request limit ([`63efb40`](https://github.com/guilatrova/gracy/commit/63efb4039a77c697510a6eb9c47c0fd3319e0017))
* Update badges/description ([`157daaf`](https://github.com/guilatrova/gracy/commit/157daafc3f12b4fa6641a5287688cd632a2ea851))

## v1.31.0 (2023-12-03)
### Feature

* Auto instantiate namespaces ([`3d26863`](https://github.com/guilatrova/gracy/commit/3d26863444a2a959a0ca6b4665c4791547acfbec))

## v1.30.0 (2023-12-02)
### Feature

* Add paginator ([`f8a1db8`](https://github.com/guilatrova/gracy/commit/f8a1db8108b2662c88c182679a71f2b4bb4476fd))

## v1.29.0 (2023-12-02)
### Feature

* Add generated_parsed_response ([`d9add8e`](https://github.com/guilatrova/gracy/commit/d9add8e208c54f72a08df5bfd4b66192c7aba751))

### Fix

* Specify parsed response type properly ([`2ef4566`](https://github.com/guilatrova/gracy/commit/2ef4566069e8f9a6bec129f393eb8b12c946b139))

### Documentation

* Explain parsed_response ([`792fd32`](https://github.com/guilatrova/gracy/commit/792fd32af1bed7ed2584f166082c6ca35d9cdaae))

## v1.28.1 (2023-11-30)
### Fix

* Resolve deploy issue ([`2ae3717`](https://github.com/guilatrova/gracy/commit/2ae3717aa4620e114e750b01eb363f49b0f0fd97))

## v1.28.0 (2023-11-30)
### Feature

* Introduce namespaces ([`67a70f3`](https://github.com/guilatrova/gracy/commit/67a70f3550a77a37d309fcb65800b1b6759fb8f8))

### Documentation

* Write about namespaces ([`468e205`](https://github.com/guilatrova/gracy/commit/468e205fddb1166770bddb4c8c241668b27d03a7))

## v1.27.1 (2023-08-08)
### Fix

* Use Union for python 3.8 ([`02415ac`](https://github.com/guilatrova/gracy/commit/02415aca8009e7bcae70f93b0c0b54c5a7d61473))

## v1.27.0 (2023-08-08)
### Feature

* Add concurrent calls ([`69976d3`](https://github.com/guilatrova/gracy/commit/69976d36d25fe45ae7b6cefe5e8800bf926aa4f8))

### Documentation

* Add note about concurrent requests ([`487dbed`](https://github.com/guilatrova/gracy/commit/487dbedbf707dbe38caf4529be1488ad72a0f2d9))

## v1.26.0 (2023-08-06)
### Feature

* Count ongoing requests ([`9495d4e`](https://github.com/guilatrova/gracy/commit/9495d4ed98839562fc470da2964d370638b6f727))

### Fix

* Use int over HttpStatus enum ([`abebe93`](https://github.com/guilatrova/gracy/commit/abebe938191b760f64490477abad595259537c79))

### Documentation

* Add common hooks ([`3a85897`](https://github.com/guilatrova/gracy/commit/3a85897baf65222e156e9762cd3f09d8bc35e2e4))

## v1.25.0 (2023-06-30)
### Feature

* **replays:** Support log events for replays ([`f9c6b80`](https://github.com/guilatrova/gracy/commit/f9c6b8071302b2262b2973830e7c3d378ae64288))

### Fix

* **reports:** Hide replays if display disabled ([`68692e0`](https://github.com/guilatrova/gracy/commit/68692e0e94ed1ec1d81ea343787abadd34ce3340))

## v1.24.1 (2023-06-30)
### Fix

* **replays:** Pass discard flag ([`2083a52`](https://github.com/guilatrova/gracy/commit/2083a524ed515758ccf1ff6b814d41eafd64ebf5))

## v1.24.0 (2023-06-30)
### Feature

* **replays:** Allow to discard bad status ([`9926ae3`](https://github.com/guilatrova/gracy/commit/9926ae3282ab9632261af204734cbfcf2fce721e))
* **loggers:** Add REPLAY placeholder ([`911a9ec`](https://github.com/guilatrova/gracy/commit/911a9ecdcb83bf877350b96d325047cea80edbd5))
* **reports:** Show replays ([`fc82bd7`](https://github.com/guilatrova/gracy/commit/fc82bd7322f35a61506bc2b42bc53c51d0ae1029))
* Track replays per request ([`e7322a3`](https://github.com/guilatrova/gracy/commit/e7322a3b31aa55d31e93e71cda43ba1ee55c53c2))
* **hooks:** Don't wait if replayed ([`785959f`](https://github.com/guilatrova/gracy/commit/785959f92a8bcacba9d7523a15c7682294bf2a00))
* **replays:** Add flag when replayed ([`8ae8394`](https://github.com/guilatrova/gracy/commit/8ae839489e338b8fbde18e96771dc7765587a6b2))

## v1.23.0 (2023-06-28)
### Feature

* **hooks:** Make reporter optional + dryrun ([`ea0ec00`](https://github.com/guilatrova/gracy/commit/ea0ec007ff5cbd19d1371fe157e9d563d50230bb))
* **hooks:** Add RateLimitBackOffHook ([`d941c68`](https://github.com/guilatrova/gracy/commit/d941c6880494c41171792fe54b9b6189bba964b9))
* Allow to modify retry-after ([`a5f043d`](https://github.com/guilatrova/gracy/commit/a5f043dc4a93fa2ade28967d8b019e483d52c6f7))
* **hooks:** Return hook result ([`ee8f102`](https://github.com/guilatrova/gracy/commit/ee8f10235bced2ed6fe8af1abe219e51950d943f))

### Fix

* **retry:** Validate log correctly ([`dbddcf5`](https://github.com/guilatrova/gracy/commit/dbddcf5f881f2873f3149bd25e4f06ad930b6c51))

## v1.22.0 (2023-06-27)
### Feature

* **retry:** Add RETRY_CAUSE to log ([`3bf1c72`](https://github.com/guilatrova/gracy/commit/3bf1c72776874b3b77f5620ce57e34a912e0a197))
* **retry:** Add cause ([`2b468fd`](https://github.com/guilatrova/gracy/commit/2b468fde1c1a8d3374268c54814bdfb4f992f191))

### Fix

* **retry:** Check override for status ([`1b9383d`](https://github.com/guilatrova/gracy/commit/1b9383d0e340c3a422e87f69c17ab1e097ca7112))
* Make it compatible with py3.8 ([`96e3e61`](https://github.com/guilatrova/gracy/commit/96e3e610eee324047c63adcddd5a6fa534cb51a8))
* **retry:** Set last rep for overriden retry ([`d0241e5`](https://github.com/guilatrova/gracy/commit/d0241e54a4f6cbd5f0b5541366cae3c3a64eb6c3))
* Typo and wrong attrs ([`07d30e0`](https://github.com/guilatrova/gracy/commit/07d30e09a4767084bbfcca5654682486b579bb42))

## v1.21.1 (2023-06-26)
### Fix

* Expose OverrideRetryOn ([`0f14782`](https://github.com/guilatrova/gracy/commit/0f14782aa870efcec829cd9efb2bc72ea101305e))

## v1.21.0 (2023-06-26)
### Feature

* **hooks:** Implement flag for diff locks ([`dcce610`](https://github.com/guilatrova/gracy/commit/dcce6104f1129ee4710b50fc034c1a8629f57cf2))

## v1.20.0 (2023-06-26)
### Feature

* **hooks:** Handle dates for retry_after header ([`3feef65`](https://github.com/guilatrova/gracy/commit/3feef652b1de9aa32b551c54307965f24a92e1b7))
* **hooks:** Add a common hook ([`04734ad`](https://github.com/guilatrova/gracy/commit/04734adcbea3ecc3cde93e1330f58992bb20b3be))
* **retry:** Implement retry override ([`0045413`](https://github.com/guilatrova/gracy/commit/004541385ca1956d6d3cc3c4807ec09cf9999177))

## v1.19.0 (2023-06-11)
### Feature

* Include cause/context to the req exception ([`b2a96f1`](https://github.com/guilatrova/gracy/commit/b2a96f1caba229ef8ae2b102f160b107bde1f9dd))

## v1.18.0 (2023-06-10)
### Feature

* Create request failed exc ([`8087365`](https://github.com/guilatrova/gracy/commit/8087365ebbefa1de8801fc232553e1a5f5d954b5))

### Fix

* **replays:** Handle content type none ([`0229218`](https://github.com/guilatrova/gracy/commit/02292182f99d925ea3cb3d916ba940489a6984e0))

## v1.17.3 (2023-05-31)
### Fix

* **deps:** Requires httpx>=0.23 ([`c3fc7c0`](https://github.com/guilatrova/gracy/commit/c3fc7c08387204eee825a5b6bd17d10d41706d78))

## v1.17.2 (2023-05-18)
### Fix
* Don't pass every kwargs to build_request ([`192838e`](https://github.com/guilatrova/gracy/commit/192838ed0dada8f0dbf30aa7a15a42efe7ad50b0))

## v1.17.1 (2023-05-17)
### Fix
* Support py38+ ([`1ac2f56`](https://github.com/guilatrova/gracy/commit/1ac2f56741094e5c0b4586093f8fa8cb26720d02))

## v1.17.0 (2023-05-13)
### Feature
* Count/display data about replays ([`658b5f5`](https://github.com/guilatrova/gracy/commit/658b5f52adf34d5d6e577abc7e6b9cd33a745919))
* Implement skip throttle ([`efb259c`](https://github.com/guilatrova/gracy/commit/efb259c151ee0807af325901155b67184de062e2))

## v1.16.0 (2023-05-13)
### Feature
* Pass retry state to after hook ([`218e510`](https://github.com/guilatrova/gracy/commit/218e510d84a3acf1fbee84141ec5f7123672cd6b))
* Implement gracy hooks ([`243dddb`](https://github.com/guilatrova/gracy/commit/243dddbd58845cbc92a0b84eaf44c612a125daf5))
* Implement hooks ([`7a88e4c`](https://github.com/guilatrova/gracy/commit/7a88e4c93e3517b9ed095f179582f0fb2809e48a))

### Fix
* Resolve recursion for hooks ([`e1be02b`](https://github.com/guilatrova/gracy/commit/e1be02bb20395c207353de2ea3bae8d839a34c03))

### Documentation
* **hooks:** Add example about hook ([`3cb52db`](https://github.com/guilatrova/gracy/commit/3cb52db7600213252bb36d6de8442bd487fd57b7))

## v1.15.0 (2023-05-11)
### Feature
* Show 'Aborts' as title ([`8485409`](https://github.com/guilatrova/gracy/commit/8485409e899e5d4591754ad62e35cfa4a128f124))
* **reports:** Show retries/throttles ([`f6de12a`](https://github.com/guilatrova/gracy/commit/f6de12a51a95b7c0ac8d0302004a3ad8c0d2e146))

## v1.14.0 (2023-05-11)
### Feature
* Default safe format + retry status code ([`5d7f834`](https://github.com/guilatrova/gracy/commit/5d7f834db146284813341d55979e25b373855606))
* Display aborted requests ([`67ac1ed`](https://github.com/guilatrova/gracy/commit/67ac1ed103248a8f65890826fc6732ec20adb683))

### Documentation
* Add note about graceful request ([`7e14c80`](https://github.com/guilatrova/gracy/commit/7e14c80205bd56df9297d2a169c3529397b4f05a))

## v1.13.0 (2023-05-10)
### Feature
* Track broken requests ([`e40d8b8`](https://github.com/guilatrova/gracy/commit/e40d8b8774c86f766c69c0cd8f0d5d5b65f09d0f))
* Capture broken requests (without a response) ([`bf0ac44`](https://github.com/guilatrova/gracy/commit/bf0ac44e87f96d7acc41f7f0e63411ac0f113a67))

## v1.12.0 (2023-05-04)
### Feature
* Improve decorator typing ([`72233d6`](https://github.com/guilatrova/gracy/commit/72233d60dd84cfddf2778b585b1260833f357c1e))

## v1.11.4 (2023-05-04)
### Fix
* Add support for `graceful_generator` ([`22ecf9a`](https://github.com/guilatrova/gracy/commit/22ecf9ac91064fcc4288f38ff73a77f4e165b98d))

## v1.11.3 (2023-03-24)
### Fix
* Make exception pickable ([`16d6a62`](https://github.com/guilatrova/gracy/commit/16d6a6248fd46a565c411743a3bf0f74dac94363))

### Documentation
* Show custom request timeout ([`e2a069b`](https://github.com/guilatrova/gracy/commit/e2a069b46a01cbcbf5bd2a9507d7d25505ecbd83))

## v1.11.2 (2023-03-03)
### Fix
* Log exhausted when appropriate ([`8c5d622`](https://github.com/guilatrova/gracy/commit/8c5d622fef7aa6dd2514cfaaf867445f56d7b04a))
* Retry considers last validation result ([`595177f`](https://github.com/guilatrova/gracy/commit/595177f50e396f4ca7b2dcc1c8ed535928a0aca7))
* Handle retry edge case ([`077e6f4`](https://github.com/guilatrova/gracy/commit/077e6f49d80cb6d886c31aa010a4f814a6953445))
* Retry result is used as response ([`8687156`](https://github.com/guilatrova/gracy/commit/8687156991058fa24043dc39658f0a12377a21f6))

### Documentation
* Add httpbin example ([`1babd10`](https://github.com/guilatrova/gracy/commit/1babd1098a46c4d0bc24ed228d76bb094260ad5e))

## v1.11.1 (2023-02-23)
### Fix
* **retry:** Don't retry when successful ([`b334c22`](https://github.com/guilatrova/gracy/commit/b334c227a4a8a688029130c736118b6dcb4f8f3b))
* **pymongo:** Adjust filter ([`5ee9f0c`](https://github.com/guilatrova/gracy/commit/5ee9f0c6aa523530929bd69d19a9ff637c46705c))
* **pymongo:** Use correct methods/kwargs ([`4a191d8`](https://github.com/guilatrova/gracy/commit/4a191d81e083772add036bc3d9d5937ccbf6d31c))

### Documentation
* Update examples ([`26420da`](https://github.com/guilatrova/gracy/commit/26420da78776862a0cb7569b5f64b610ed212ff6))

## v1.11.0 (2023-02-23)
### Feature
* Enable config debugging flag ([`07c6339`](https://github.com/guilatrova/gracy/commit/07c633923a20343329aa884ddc109f3cde0e5be0))

## v1.10.1 (2023-02-23)
### Fix
* Error log ([`6f63941`](https://github.com/guilatrova/gracy/commit/6f6394181ed024f738605a4743af2eea788ce4f7))

## v1.10.0 (2023-02-22)
### Feature
* Allow custom validators ([`50818f8`](https://github.com/guilatrova/gracy/commit/50818f89fe2a03800fde18fa38686a04853cb54a))

### Fix
* Implement proper validate/retry/parse logic ([`0b2fa75`](https://github.com/guilatrova/gracy/commit/0b2fa75228c9340efb8595fee801c0cfa3303619))
* Raise exception correctly ([`10a90b5`](https://github.com/guilatrova/gracy/commit/10a90b5159a2fce3e24c1bfac7f4b9e0cb58d059))

### Documentation
* Add exception details to retry params ([`8d69234`](https://github.com/guilatrova/gracy/commit/8d692346369b5c83d05e746ec1b7e9f924d02cbd))
* Enhance custom validator example ([`d5e02eb`](https://github.com/guilatrova/gracy/commit/d5e02eb032739639f9ceb655b5b88c39f8c9a0f6))
* Add validators ([`e3e8fa6`](https://github.com/guilatrova/gracy/commit/e3e8fa672e5f95d02f60dc3af762b6e6cd189d4d))

## v1.9.1 (2023-02-21)
### Fix
* Create tuples ([`f648f85`](https://github.com/guilatrova/gracy/commit/f648f85a5787b2cd86934051640e666815fe5864))

## v1.9.0 (2023-02-21)
### Feature
* Make exceptions pickable ([`5ab62c5`](https://github.com/guilatrova/gracy/commit/5ab62c59ac273078e7a1ef3122e76bf0c6901e70))

### Documentation
* Reword ([`0ca061f`](https://github.com/guilatrova/gracy/commit/0ca061f1b1e73c73b01808e2d9f0258f03e0fefa))
* Add a emoji ([`8da07ae`](https://github.com/guilatrova/gracy/commit/8da07aecd8da6642edf01a94475ff49f297c1886))
* Reword ([`a54f1f7`](https://github.com/guilatrova/gracy/commit/a54f1f7bac2b7a5fb52485b31c746e58734066d0))
* Reorder logging customization ([`f6d9d76`](https://github.com/guilatrova/gracy/commit/f6d9d765daee63e7e863426519f8acda5bc2c5f0))

## v1.8.1 (2023-02-17)
### Fix
* Retry logic triggers only once ([`0fc2358`](https://github.com/guilatrova/gracy/commit/0fc2358b1631eacc0587a59afe1d21b419f8679e))

## v1.8.0 (2023-02-17)
### Feature
* Calculate throttling await properly ([`ba520e0`](https://github.com/guilatrova/gracy/commit/ba520e034bab88b2b5a258473f8a2ba7ff7c5879))
* Lock throttling logs properly ([`a8ebd69`](https://github.com/guilatrova/gracy/commit/a8ebd69df0e5184a6a806870a12888c202ba37d8))
* Prevent floats for max_requests ([`b9aed74`](https://github.com/guilatrova/gracy/commit/b9aed746bdfcd672920baeb047cf02b31e146503))
* Format rule time range ([`514cbae`](https://github.com/guilatrova/gracy/commit/514cbaeeb2d02de12f60a62e8285ce0ba1ad0437))
* Allow custom time windows for throttling ([`7fc35f0`](https://github.com/guilatrova/gracy/commit/7fc35f09e4a5e8df50a746cf95d112b08d4dd9bc))

### Fix
* Correct kwargs ([`0db5925`](https://github.com/guilatrova/gracy/commit/0db59254081d479a20c411ab346cad605e3a2efb))

### Documentation
* Add `THROTTLE_TIME_RANGE` ([`299c200`](https://github.com/guilatrova/gracy/commit/299c2008b5da43e7a52035dc285375b0b1dfc093))
* **throttling:** Add timedelta example ([`74c20ef`](https://github.com/guilatrova/gracy/commit/74c20ef91c521165b72c999c7212268ca83ec7cc))
* Enhance throttling example ([`200b3c5`](https://github.com/guilatrova/gracy/commit/200b3c5adac8a16f3af002d56f2e3c8b84f3f0d3))

## v1.7.1 (2023-02-14)
### Fix
* **retry:** Remove duplicated default msg ([`963d7e8`](https://github.com/guilatrova/gracy/commit/963d7e8237a85c5f5692a01d7a3d1c0eb733b752))

### Documentation
* Fix reports/replay order ([`b4ddf79`](https://github.com/guilatrova/gracy/commit/b4ddf792fe29ae49e981fda5b1fca0bec4aca0f9))

## v1.7.0 (2023-02-12)
### Feature
* Handle missing replays ([`4395b83`](https://github.com/guilatrova/gracy/commit/4395b832cd9f75a88d696d5cba2eb7bd9f7ce61d))
* Report show replay mode ([`b488975`](https://github.com/guilatrova/gracy/commit/b4889755c75c3f3a14507b27b3d57ba243b5c828))
* Implement replay load w/ sqlite ([`4fa4cf6`](https://github.com/guilatrova/gracy/commit/4fa4cf6983ed64d82560d47c813bfeb4cfa5ed66))
* Implement replay (store only) w/ sqlite ([`797c2b9`](https://github.com/guilatrova/gracy/commit/797c2b95334f5ebfd9b17555278b1be44b7eeef2))

### Fix
* Handle 0 requests for logger printer ([`09e471c`](https://github.com/guilatrova/gracy/commit/09e471c791e34c9b30427c6903bb19c8c25338aa))

### Documentation
* Add details about custom replay storage ([`f03407f`](https://github.com/guilatrova/gracy/commit/f03407fbd66a40850d679541b0616fc7847c8b5c))
* Add brief explanation about replay ([`edd1a24`](https://github.com/guilatrova/gracy/commit/edd1a24fb255d8ed23288f277769b923e6af218b))

## v1.6.1 (2023-02-11)
### Fix
* Gracy supports Python >=3.8 ([`a3623a9`](https://github.com/guilatrova/gracy/commit/a3623a98a7459dcba3dc78ca11917be5c6c5a82d))

## v1.6.0 (2023-02-07)
### Feature
* Handle parsing failures ([`ac48952`](https://github.com/guilatrova/gracy/commit/ac489522a98412d65b85ac3317dbe6083d8819ad))

### Documentation
* Fix syntax ([`9996b39`](https://github.com/guilatrova/gracy/commit/9996b39f505d3221f2e63d78bd311e90f2608349))

## v1.5.0 (2023-02-05)
### Feature
* Protect lambda custom msg from unknown keys ([`d6da853`](https://github.com/guilatrova/gracy/commit/d6da8536d1b561fd606d2911749d99309aa92460))
* Implement lambda for loggers ([`e7d9248`](https://github.com/guilatrova/gracy/commit/e7d9248475ce9dab92913cc7fa7eb6554c9676d7))

### Fix
* Use correct typing for coroutine ([`65296cd`](https://github.com/guilatrova/gracy/commit/65296cdddf925126ea47e591f7def242b0e6b6da))

### Documentation
* Add report examples ([`269810c`](https://github.com/guilatrova/gracy/commit/269810c4d205e5356672287f08c3d34d3bc0c3f0))

## v1.4.0 (2023-02-05)
### Feature
* Implement the logger printer ([`40298f5`](https://github.com/guilatrova/gracy/commit/40298f5204a499730f93d2d79bbfed43dc754b0c))
* Implement the list printer ([`9adee2d`](https://github.com/guilatrova/gracy/commit/9adee2d9ea78a569ab1541724b86fb73b06a4f2e))
* Split rich as optional dep ([`ae169df`](https://github.com/guilatrova/gracy/commit/ae169df066871d4095b95c032e7ec06b85ab3249))

### Documentation
* Fix bad information ([`e1a6746`](https://github.com/guilatrova/gracy/commit/e1a67466a9403dc87719cde9079a0f2b0ed7b16f))
* Fix bad syntax example ([`116b9bf`](https://github.com/guilatrova/gracy/commit/116b9bf0e1ed6fabdb9e5d365ade7d92ab8d3429))

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
