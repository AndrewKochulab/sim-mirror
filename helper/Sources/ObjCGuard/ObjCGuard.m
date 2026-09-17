// SPDX-License-Identifier: Apache-2.0
#import "ObjCGuard.h"

NSException *_Nullable SMGuard(NS_NOESCAPE void (^body)(void)) {
  @try {
    body();
  } @catch (NSException *exception) {
    return exception;
  }
  return nil;
}
