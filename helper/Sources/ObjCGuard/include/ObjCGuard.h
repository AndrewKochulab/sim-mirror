// SPDX-License-Identifier: Apache-2.0
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Runs `body`, answering the Objective-C exception it raised, or nil when it raised none.
///
/// Apple's simulator frameworks raise exceptions from remote proxies when a device goes away mid-call; Swift cannot
/// catch them, so every call into those frameworks that can raise goes through here.
NSException *_Nullable SMGuard(NS_NOESCAPE void (^body)(void));

NS_ASSUME_NONNULL_END
